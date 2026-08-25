"""
本模块验证智能陪伴聊天核心的提示组装、网络协议和安全边界。

大部分响应由内存假对象提供；重定向测试只访问进程内
``127.0.0.1`` 临时服务。测试不访问外网、不创建 GUI、不写入密钥或用户档案。
"""

from __future__ import annotations

import json
import threading
import urllib.error
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from onepic_desktop_pet.companion import chat
from onepic_desktop_pet.companion.chat import (
    CancellationToken,
    ChatCancelledError,
    ChatProtocolError,
    ChatTransportError,
    build_chat_payload,
    build_messages,
    chat_completion,
    iter_sse_data,
    parse_assistant_response,
    parse_sse_event,
    resolve_chat_completions_url,
    sanitize_error_message,
)


class FakeResponse:
    """为 ``urllib`` 提供可上下文管理的内存响应。"""

    def __init__(
        self,
        *,
        body: bytes = b"",
        lines: tuple[bytes, ...] = (),
    ) -> None:
        self.body = body
        self.lines = lines
        self.status = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]

    def __iter__(self) -> Iterator[bytes]:
        return iter(self.lines)


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "你是测试桌宠。"},
        {"role": "user", "content": "你好"},
    ]


def _sse(content: str) -> bytes:
    payload = {"choices": [{"delta": {"content": content}}]}
    return (
        "data: "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    ).encode("utf-8")


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://example.test:99999/v1",
        "https://example.test:0/v1",
        "https://[not-an-ipv6]/v1",
    ),
)
def test_endpoint_rejects_invalid_host_or_port(endpoint: str) -> None:
    """畸形主机或超范围端口必须在进入 Qt/密钥层前统一拒绝。"""

    with pytest.raises(ValueError, match="主机|端口"):
        resolve_chat_completions_url(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://example.com/v1",
        "http://192.0.2.10:8080/v1",
        "http://[2001:db8::1]/v1",
    ),
)
def test_endpoint_rejects_remote_plaintext_http(endpoint: str) -> None:
    """Bearer 密钥和消息不得发送到远程明文 HTTP 服务。"""

    with pytest.raises(ValueError, match="https|回环"):
        resolve_chat_completions_url(endpoint)


def test_build_messages_includes_profiles_memories_bond_and_preset() -> None:
    history = [
        {"role": "system", "content": "不得夹带的旧 system"},
        {"role": "user", "content": "昨天好"},
        {"role": "tool", "content": "不得夹带的 tool"},
        {"role": "assistant", "content": "今天也好"},
    ]

    messages = build_messages(
        "帮我安排今天",
        history=history,
        character_profile={"name": "阿羽", "temperament": "沉稳"},
        user_profile={"name": "委托人", "preference": "简洁"},
        selected_memories=["上周完成了绘画", {"fact": "喜欢茶"}],
        affection=73,
        bond={"stage": "trusted"},
        preset_prompt="整理三件最重要的事",
    )

    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert [item["role"] for item in messages[1:]] == [
        "user",
        "assistant",
        "user",
    ]
    assert "阿羽" in system
    assert "委托人" in system
    assert "上周完成了绘画" in system
    assert "好感：73" in system
    assert "trusted" in system
    assert "整理三件最重要的事" in system
    assert "不得夹带的旧 system" not in system

    zero_affection = build_messages(
        "记录当前关系",
        character_profile="测试角色",
        affection=0,
    )[0]["content"]
    assert "好感：0" in zero_affection


def test_payload_is_stable_and_rejects_privileged_message_roles() -> None:
    payload = build_chat_payload(
        _messages(),
        model="demo-model",
        stream=True,
        temperature=0.25,
        max_tokens=321,
    )

    assert payload == {
        "model": "demo-model",
        "messages": _messages(),
        "stream": True,
        "temperature": 0.25,
        "max_tokens": 321,
    }
    with pytest.raises(ValueError, match="不支持的消息角色"):
        build_chat_payload(
            [{"role": "tool", "content": "rm"}],
            model="demo-model",
        )


def test_non_stream_request_payload_and_response_are_openai_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    service_content = (
        "当然，我陪你。"
        '<pet_hint>{"emotion":"happy","action":"wave"}</pet_hint>'
    )
    response_body = json.dumps(
        {"choices": [{"message": {"content": service_content}}]},
        ensure_ascii=False,
    ).encode("utf-8")

    def fake_urlopen(
        request: object,
        *,
        timeout: float,
    ) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(body=response_body)

    monkeypatch.setattr(chat, "_open_no_redirect", fake_urlopen)

    reply = chat_completion(
        endpoint="https://chat.example.test/v1",
        api_key="unit-test-secret",
        model="demo-model",
        messages=_messages(),
        timeout=7.5,
        max_tokens=100,
    )

    request = captured["request"]
    assert request.full_url == "https://chat.example.test/v1/chat/completions"
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer unit-test-secret"
    request_payload = json.loads(request.data.decode("utf-8"))
    assert request_payload["model"] == "demo-model"
    assert request_payload["messages"] == _messages()
    assert request_payload["stream"] is False
    assert "unit-test-secret" not in request.data.decode("utf-8")
    assert captured["timeout"] == 7.5
    assert reply.text == "当然，我陪你。"
    assert reply.hint is not None
    assert reply.hint.emotion == "happy"
    assert reply.hint.action == "wave"


def test_sse_helpers_support_comments_multiline_and_done() -> None:
    events = list(
        iter_sse_data(
            [
                b": keep-alive\n",
                b"data: first\n",
                b"data: second\n",
                b"\n",
                b"event: ignored\n",
                b"data: [DONE]\n",
                b"\n",
            ]
        )
    )

    assert events == ["first\nsecond", "[DONE]"]
    assert parse_sse_event("[DONE]").done is True
    delta = parse_sse_event(
        json.dumps({"choices": [{"delta": {"content": "嗨"}}]})
    )
    assert delta.delta == "嗨"
    assert delta.done is False

    encoded = "data: 你好\n\n".encode("utf-8")
    split_at = encoded.index("你".encode("utf-8")) + 1
    assert list(iter_sse_data([encoded[:split_at], encoded[split_at:]])) == [
        "你好"
    ]


def test_streaming_collects_text_hides_control_marker_and_parses_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visible_deltas: list[str] = []
    fake_response = FakeResponse(
        lines=(
            _sse("你"),
            _sse("好！"),
            _sse("<pet_"),
            _sse('hint>{"emotion":"curious",'),
            _sse('"action":"wait_response"}</pet_hint>'),
            b"data: [DONE]\n\n",
        )
    )
    monkeypatch.setattr(
        chat,
        "_open_no_redirect",
        lambda request, timeout: fake_response,
    )

    reply = chat_completion(
        endpoint="http://127.0.0.1:11434/v1/chat/completions",
        api_key=None,
        model="local-model",
        messages=_messages(),
        stream=True,
        on_delta=visible_deltas.append,
    )

    assert "".join(visible_deltas) == "你好！"
    assert "pet_hint" not in "".join(visible_deltas)
    assert reply.text == "你好！"
    assert reply.hint is not None
    assert reply.hint.emotion == "curious"
    assert reply.hint.action == "wait_response"


def test_parser_only_accepts_trailing_marker_and_hard_whitelist() -> None:
    plain = parse_assistant_response(
        '这只是正文里的 {"action":"wave"}，不是控制标记。'
    )
    unsafe = parse_assistant_response(
        '我知道了。<pet_hint>{"action":"open_url"}</pet_hint>',
        allowed_actions={"open_url", "wave"},
    )
    extra_field = parse_assistant_response(
        '我知道了。<pet_hint>{"action":"wave","command":"calc"}</pet_hint>'
    )
    valid_narrowed = parse_assistant_response(
        '我挥挥手。<pet_hint>{"emotion":"happy","action":"wave"}</pet_hint>',
        allowed_emotions={"happy"},
        allowed_actions={"wave"},
    )

    assert plain.hint is None
    assert unsafe.text == "我知道了。"
    assert unsafe.hint is None
    assert extra_field.hint is None
    assert valid_narrowed.hint is not None
    assert valid_narrowed.hint.action == "wave"


def test_transport_error_and_sanitizer_never_expose_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = "sk-unit-test-secret-123456"
    raw_error = (
        "Authorization: Bearer leaked-token-1234 "
        f"api_key={api_key} https://user:password@example.test/"
    )
    safe = sanitize_error_message(raw_error, secrets=(api_key,))
    assert api_key not in safe
    assert "leaked-token-1234" not in safe
    assert "password" not in safe

    monkeypatch.setattr(
        chat,
        "_open_no_redirect",
        lambda request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError(f"Bearer {api_key}")
        ),
    )
    with pytest.raises(ChatTransportError) as captured:
        chat_completion(
            endpoint="https://chat.example.test/v1",
            api_key=api_key,
            model="demo-model",
            messages=_messages(),
        )
    assert api_key not in str(captured.value)
    assert "[已脱敏]" in str(captured.value)

    echoed_error = json.dumps(
        {"error": {"message": f"provider echoed {api_key}"}}
    ).encode("utf-8")
    monkeypatch.setattr(
        chat,
        "_open_no_redirect",
        lambda request, timeout: FakeResponse(body=echoed_error),
    )
    with pytest.raises(ChatProtocolError) as protocol_error:
        chat_completion(
            endpoint="https://chat.example.test/v1",
            api_key=api_key,
            model="demo-model",
            messages=_messages(),
        )
    assert api_key not in str(protocol_error.value)


def test_cancelled_request_never_opens_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = False

    def forbidden_urlopen(request: object, timeout: float) -> FakeResponse:
        nonlocal opened
        opened = True
        raise AssertionError("不应访问网络")

    monkeypatch.setattr(chat, "_open_no_redirect", forbidden_urlopen)
    token = CancellationToken()
    token.cancel()

    with pytest.raises(ChatCancelledError, match="已取消"):
        chat_completion(
            endpoint="https://chat.example.test/v1",
            api_key="unused",
            model="demo-model",
            messages=_messages(),
            cancel_token=token,
        )
    assert opened is False


def test_malformed_json_and_unfinished_sse_are_protocol_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat,
        "_open_no_redirect",
        lambda request, timeout: FakeResponse(body=b"not-json"),
    )
    with pytest.raises(ChatProtocolError, match="无效 JSON"):
        chat_completion(
            endpoint="https://chat.example.test/v1",
            api_key=None,
            model="demo-model",
            messages=_messages(),
        )

    monkeypatch.setattr(
        chat,
        "_open_no_redirect",
        lambda request, timeout: FakeResponse(lines=(_sse("未完成"),)),
    )
    with pytest.raises(ChatProtocolError, match="意外结束"):
        chat_completion(
            endpoint="https://chat.example.test/v1",
            api_key=None,
            model="demo-model",
            messages=_messages(),
            stream=True,
        )


def test_standard_library_client_rejects_cross_origin_redirect() -> None:
    """标准库公共接口也不得把 Bearer 转发到另一服务来源。"""

    target_requests: list[str | None] = []
    initial_authorization: list[str | None] = []

    class _TargetHandler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            target_requests.append(self.headers.get("Authorization"))
            body = json.dumps(
                {"choices": [{"message": {"content": "不应到达"}}]}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _respond
        do_POST = _respond

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    target = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
    target.daemon_threads = True
    target_host, target_port = target.server_address
    target_url = f"http://{target_host}:{target_port}/chat/completions"

    class _RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server 固定接口
            initial_authorization.append(self.headers.get("Authorization"))
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(302)
            self.send_header("Location", target_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    redirect.daemon_threads = True
    redirect_host, redirect_port = redirect.server_address
    threads = [
        threading.Thread(target=target.serve_forever, daemon=True),
        threading.Thread(target=redirect.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()
    secret = "redirect-proof-secret"
    try:
        with pytest.raises(ChatTransportError) as error:
            chat_completion(
                endpoint=f"http://{redirect_host}:{redirect_port}/v1",
                api_key=secret,
                model="local-model",
                messages=[{"role": "user", "content": "hello"}],
            )
    finally:
        redirect.shutdown()
        target.shutdown()
        redirect.server_close()
        target.server_close()
        for thread in threads:
            thread.join(timeout=1.0)

    assert error.value.status_code == 302
    assert "拒绝自动重定向" in str(error.value)
    assert initial_authorization == [f"Bearer {secret}"]
    assert target_requests == []
