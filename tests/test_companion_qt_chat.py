"""
本模块验证 Qt 聊天适配层的 JSON/SSE、脱敏、上限与立即取消。

所有网络请求只访问测试进程内的 ``127.0.0.1`` 临时 HTTP 服务，
不连接外网，不读写用户 API 密钥或配置。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.companion import qt_chat
from onepic_desktop_pet.companion.chat import ChatReply
from onepic_desktop_pet.companion.qt_chat import QtChatRequest


Responder = Callable[[BaseHTTPRequestHandler, bytes], None]


class _LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, responder: Responder) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.responder = responder
        self.requests: list[dict[str, Any]] = []

    def handle_error(self, request: object, client_address: object) -> None:
        # 客户端主动 abort 后，服务端写入可能遇到 BrokenPipe。
        return


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - http.server 固定接口
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server = self.server
        assert isinstance(server, _LocalServer)
        server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "cookie": self.headers.get("Cookie"),
                "accept": self.headers.get("Accept"),
                "body": body,
            }
        )
        server.responder(self, body)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _serve(responder: Responder) -> Iterator[tuple[str, _LocalServer]]:
    server = _LocalServer(responder)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01),
        name="onepic-qt-chat-test-server",
        daemon=True,
    )
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}/v1", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


def _send_json(
    handler: BaseHTTPRequestHandler,
    payload: object,
    *,
    status: int = 200,
) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    try:
        handler.wfile.write(raw)
        handler.wfile.flush()
    except OSError:
        pass


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "你是测试桌宠。"},
        {"role": "user", "content": "你好"},
    ]


def _application() -> QApplication:
    """统一创建离屏 QApplication，避免后续窗口测试无法升级应用类型。"""

    return QApplication.instance() or QApplication([])


def _spin_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
) -> float:
    app = _application()
    started = time.perf_counter()
    deadline = started + timeout
    while not predicate() and time.perf_counter() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.002)
    app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
    assert predicate(), f"Qt 事件在 {timeout} 秒内未到达"
    return time.perf_counter() - started


def test_json_request_uses_bearer_and_reuses_safe_reply_parser() -> None:
    def responder(handler: BaseHTTPRequestHandler, body: bytes) -> None:
        request_payload = json.loads(body.decode("utf-8"))
        assert request_payload["stream"] is False
        _send_json(
            handler,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "你好，委托人。"
                                '<pet_hint>{"emotion":"happy",'
                                '"action":"wave"}</pet_hint>'
                            )
                        }
                    }
                ]
            },
        )

    with _serve(responder) as (endpoint, server):
        request = QtChatRequest()
        replies: list[ChatReply] = []
        failures: list[str] = []
        finished: list[bool] = []
        request.succeeded.connect(replies.append)
        request.failed.connect(failures.append)
        request.finished.connect(lambda: finished.append(True))

        request.start(
            endpoint=endpoint,
            api_key="qt-test-key",
            model="local-model",
            messages=_messages(),
            allowed_emotions={"happy"},
            allowed_actions={"wave"},
        )
        _spin_until(lambda: bool(finished))

        assert failures == []
        assert len(replies) == 1
        assert replies[0].text == "你好，委托人。"
        assert replies[0].hint is not None
        assert replies[0].hint.emotion == "happy"
        assert replies[0].hint.action == "wave"
        assert server.requests[0]["path"] == "/v1/chat/completions"
        assert server.requests[0]["authorization"] == "Bearer qt-test-key"
        assert server.requests[0]["accept"] == "application/json"
        assert request.active is False


def test_sse_emits_visible_delta_but_hides_control_hint() -> None:
    def event(content: str) -> bytes:
        payload = {"choices": [{"delta": {"content": content}}]}
        return (
            "data: "
            + json.dumps(payload, ensure_ascii=False)
            + "\n\n"
        ).encode("utf-8")

    raw = b"".join(
        (
            event("你好，"),
            event("<pet_"),
            event('hint>{"action":"wave"}</pet_hint>'),
            b"data: [DONE]\n\n",
        )
    )

    def responder(handler: BaseHTTPRequestHandler, body: bytes) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        try:
            for offset in range(0, len(raw), 3):
                handler.wfile.write(raw[offset : offset + 3])
                handler.wfile.flush()
                time.sleep(0.001)
        except OSError:
            pass

    with _serve(responder) as (endpoint, _server):
        request = QtChatRequest()
        deltas: list[str] = []
        replies: list[ChatReply] = []
        failures: list[str] = []
        finished: list[bool] = []
        request.delta.connect(deltas.append)
        request.succeeded.connect(replies.append)
        request.failed.connect(failures.append)
        request.finished.connect(lambda: finished.append(True))

        request.start(
            endpoint=endpoint,
            api_key=None,
            model="local-model",
            messages=_messages(),
            stream=True,
            allowed_actions={"wave"},
        )
        _spin_until(lambda: bool(finished))

        assert failures == []
        assert "".join(deltas) == "你好，"
        assert len(replies) == 1
        assert replies[0].text == "你好，"
        assert replies[0].hint is not None
        assert replies[0].hint.action == "wave"


def test_cancel_aborts_without_waiting_for_delayed_server() -> None:
    server_received = threading.Event()

    def responder(handler: BaseHTTPRequestHandler, body: bytes) -> None:
        server_received.set()
        time.sleep(1.5)
        _send_json(
            handler,
            {"choices": [{"message": {"content": "太晚的响应"}}]},
        )

    with _serve(responder) as (endpoint, _server):
        request = QtChatRequest()
        replies: list[ChatReply] = []
        failures: list[str] = []
        finished: list[float] = []
        request.succeeded.connect(replies.append)
        request.failed.connect(failures.append)
        request.finished.connect(lambda: finished.append(time.perf_counter()))
        request.start(
            endpoint=endpoint,
            api_key=None,
            model="local-model",
            messages=_messages(),
            timeout=10,
        )
        _spin_until(server_received.is_set)

        cancelled_at = time.perf_counter()
        request.cancel()
        elapsed = _spin_until(lambda: bool(finished), timeout=0.5)

        assert elapsed < 0.25
        assert finished[0] - cancelled_at < 0.25
        assert replies == []
        assert failures == ["聊天请求已取消"]
        assert request.active is False
        request.cancel()
        assert len(finished) == 1


def test_timeout_aborts_delayed_reply_and_finishes_once() -> None:
    def responder(handler: BaseHTTPRequestHandler, body: bytes) -> None:
        time.sleep(1.0)
        _send_json(
            handler,
            {"choices": [{"message": {"content": "超时后的响应"}}]},
        )

    with _serve(responder) as (endpoint, _server):
        request = QtChatRequest()
        failures: list[str] = []
        finished: list[bool] = []
        request.failed.connect(failures.append)
        request.finished.connect(lambda: finished.append(True))
        request.start(
            endpoint=endpoint,
            api_key=None,
            model="local-model",
            messages=_messages(),
            timeout=0.1,
        )

        elapsed = _spin_until(lambda: bool(finished), timeout=0.8)
        assert elapsed < 0.5
        assert failures == ["聊天请求超时"]
        assert len(finished) == 1
        assert request.active is False


def test_redirect_is_not_followed_or_given_bearer_header() -> None:
    redirected_requests = threading.Event()

    def redirected_responder(
        handler: BaseHTTPRequestHandler,
        body: bytes,
    ) -> None:
        redirected_requests.set()
        _send_json(
            handler,
            {"choices": [{"message": {"content": "不应该到达"}}]},
        )

    with _serve(redirected_responder) as (redirected_endpoint, second_server):
        target = f"{redirected_endpoint}/chat/completions"

        def redirecting_responder(
            handler: BaseHTTPRequestHandler,
            body: bytes,
        ) -> None:
            handler.send_response(307)
            handler.send_header("Location", target)
            handler.send_header("Content-Length", "0")
            handler.end_headers()

        with _serve(redirecting_responder) as (endpoint, first_server):
            request = QtChatRequest()
            failures: list[str] = []
            finished: list[bool] = []
            request.failed.connect(failures.append)
            request.finished.connect(lambda: finished.append(True))
            request.start(
                endpoint=endpoint,
                api_key="redirect-secret-key",
                model="local-model",
                messages=_messages(),
            )
            _spin_until(lambda: bool(finished))

            assert len(first_server.requests) == 1
            assert first_server.requests[0]["authorization"] == (
                "Bearer redirect-secret-key"
            )
            assert failures == [
                "聊天服务拒绝自动重定向；"
                "请直接填写最终 Chat Completions 地址"
            ]
            assert redirected_requests.is_set() is False
            assert second_server.requests == []


def test_reused_qt_client_does_not_share_service_cookie_between_requests() -> None:
    call_count = 0

    def responder(handler: BaseHTTPRequestHandler, body: bytes) -> None:
        nonlocal call_count
        call_count += 1
        raw = json.dumps(
            {"choices": [{"message": {"content": f"reply-{call_count}"}}]}
        ).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        if call_count == 1:
            handler.send_header("Set-Cookie", "session=pet-a; Path=/")
        handler.end_headers()
        handler.wfile.write(raw)
        handler.wfile.flush()

    with _serve(responder) as (endpoint, server):
        request = QtChatRequest()
        replies: list[ChatReply] = []
        finished: list[bool] = []
        request.succeeded.connect(replies.append)
        request.finished.connect(lambda: finished.append(True))

        request.start(
            endpoint=endpoint,
            api_key=None,
            model="local-model",
            messages=_messages(),
        )
        _spin_until(lambda: len(finished) == 1)
        request.start(
            endpoint=endpoint,
            api_key=None,
            model="local-model",
            messages=_messages(),
        )
        _spin_until(lambda: len(finished) == 2)

        assert [reply.text for reply in replies] == ["reply-1", "reply-2"]
        assert [entry["cookie"] for entry in server.requests] == [None, None]


def test_http_error_redacts_key_and_response_limit_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-qt-super-secret-123456"

    def secret_error(handler: BaseHTTPRequestHandler, body: bytes) -> None:
        _send_json(
            handler,
            {
                "error": {
                    "message": f"Bearer {secret} api_key={secret}"
                }
            },
            status=401,
        )

    with _serve(secret_error) as (endpoint, _server):
        request = QtChatRequest()
        failures: list[str] = []
        finished: list[bool] = []
        request.failed.connect(failures.append)
        request.finished.connect(lambda: finished.append(True))
        request.start(
            endpoint=endpoint,
            api_key=secret,
            model="local-model",
            messages=_messages(),
        )
        _spin_until(lambda: bool(finished))

        assert len(failures) == 1
        assert "HTTP 401" in failures[0]
        assert secret not in failures[0]
        assert "[已脱敏]" in failures[0]

    monkeypatch.setattr(qt_chat, "_MAX_RESPONSE_BYTES", 128)

    def oversized(handler: BaseHTTPRequestHandler, body: bytes) -> None:
        _send_json(
            handler,
            {
                "choices": [
                    {"message": {"content": "x" * 512}}
                ]
            },
        )

    with _serve(oversized) as (endpoint, _server):
        request = QtChatRequest()
        failures = []
        finished = []
        request.failed.connect(failures.append)
        request.finished.connect(lambda: finished.append(True))
        request.start(
            endpoint=endpoint,
            api_key=None,
            model="local-model",
            messages=_messages(),
        )
        _spin_until(lambda: bool(finished))

        assert failures == ["聊天服务响应超过大小上限"]
        assert len(finished) == 1
