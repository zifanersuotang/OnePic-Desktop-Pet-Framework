"""
本模块提供不需要 ``QThread`` 的 Qt 聊天网络适配层。

请求由 ``QNetworkAccessManager`` 在 Qt 事件循环内异步执行；取消和
超时都会立即调用 ``QNetworkReply.abort()``，不等待服务端结束。本层
复用 :mod:`onepic_desktop_pet.companion.chat` 的请求体校验、JSON/SSE
解析、表情/动作白名单和错误脱敏边界，不读写密钥或磁盘。
每次请求禁止 Cookie 读写与认证缓存复用，避免同来源不同宠物串会话。
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

from .chat import (
    ChatError,
    ChatProtocolError,
    ChatReply,
    _MAX_RESPONSE_BYTES,
    _MAX_STREAM_TEXT_CHARS,
    _VisibleDeltaBuffer,
    _extract_service_error,
    _parse_json_response,
    build_chat_payload,
    parse_assistant_response,
    parse_sse_event,
    resolve_chat_completions_url,
    sanitize_error_message,
)


_MAX_ERROR_BODY_BYTES: Final[int] = 16_384


class _IncrementalSseParser:
    """将 ``QNetworkReply.readyRead`` 交付的任意分块还原为 SSE data 事件。"""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(
            errors="replace"
        )
        self._pending = ""
        self._data_lines: list[str] = []
        self._closed = False

    def feed(self, chunk: bytes, *, final: bool = False) -> list[str]:
        """添加一个字节分块，返回此次刚完整组装的 data 事件。"""

        if self._closed:
            return []
        self._pending += self._decoder.decode(chunk, final=final)
        events: list[str] = []
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            event = self._consume_line(line)
            if event is not None:
                events.append(event)
        if final:
            if self._pending:
                event = self._consume_line(self._pending)
                self._pending = ""
                if event is not None:
                    events.append(event)
            if self._data_lines:
                events.append("\n".join(self._data_lines))
                self._data_lines.clear()
            self._closed = True
        return events

    def _consume_line(self, raw_line: str) -> str | None:
        line = raw_line.rstrip("\r")
        if not line:
            if not self._data_lines:
                return None
            event = "\n".join(self._data_lines)
            self._data_lines.clear()
            return event
        if line.startswith(":"):
            return None
        field, separator, value = line.partition(":")
        if field != "data":
            return None
        if separator and value.startswith(" "):
            value = value[1:]
        self._data_lines.append(value)
        return None


class QtChatRequest(QObject):
    """在 Qt 主线程中执行一次 OpenAI-compatible 聊天请求。

    对象可在上一次 ``finished`` 后再次 ``start``。输入校验失败会
    从 ``start`` 直接抛出 ``ValueError``；网络、协议、取消和超时结果
    则通过信号交付。
    """

    delta = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        manager: QNetworkAccessManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager or QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)
        self._active = False
        self._stream = False
        self._api_key = ""
        self._allowed_emotions: tuple[str, ...] | None = None
        self._allowed_actions: tuple[str, ...] | None = None
        self._raw = bytearray()
        self._received_bytes = 0
        self._sse_parser: _IncrementalSseParser | None = None
        self._sse_text: list[str] = []
        self._sse_text_chars = 0
        self._visible: _VisibleDeltaBuffer | None = None
        self._saw_done = False

    @property
    def active(self) -> bool:
        """返回是否存在尚未结束的网络请求。"""

        return self._active

    def start(
        self,
        *,
        endpoint: str,
        api_key: str | None,
        model: str,
        messages: Sequence[Mapping[str, object]],
        stream: bool = False,
        timeout: float = 30.0,
        temperature: float | None = 0.8,
        max_tokens: int | None = None,
        allowed_emotions: Iterable[str] | None = None,
        allowed_actions: Iterable[str] | None = None,
    ) -> None:
        """校验参数并启动异步请求；方法本身不阻塞。"""

        if self._active:
            raise RuntimeError("已有聊天请求在进行中")
        clean_timeout = float(timeout)
        if not 0.1 <= clean_timeout <= 600.0:
            raise ValueError("timeout 必须在 0.1 到 600 秒之间")
        payload = build_chat_payload(
            messages,
            model=model,
            stream=stream,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        url_text = resolve_chat_completions_url(endpoint)
        request_data = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        clean_api_key = str(api_key or "").strip()

        request = QNetworkRequest(QUrl(url_text))
        # 禁用自动重定向，避免 Authorization 在跨 origin 跳转时被
        # 下游服务接收。接口迁移时必须由用户填写最终端点。
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        for attribute in (
            QNetworkRequest.Attribute.CookieLoadControlAttribute,
            QNetworkRequest.Attribute.CookieSaveControlAttribute,
            QNetworkRequest.Attribute.AuthenticationReuseAttribute,
        ):
            request.setAttribute(
                attribute,
                QNetworkRequest.LoadControl.Manual,
            )
        request.setRawHeader(
            QByteArray(b"Accept"),
            QByteArray(
                b"text/event-stream" if stream else b"application/json"
            ),
        )
        request.setRawHeader(
            QByteArray(b"Content-Type"),
            QByteArray(b"application/json; charset=utf-8"),
        )
        request.setRawHeader(
            QByteArray(b"User-Agent"),
            QByteArray(b"OnePicDesktopPet/companion-qt"),
        )
        if clean_api_key:
            request.setRawHeader(
                QByteArray(b"Authorization"),
                QByteArray(f"Bearer {clean_api_key}".encode("utf-8")),
            )

        self._reset_buffers(stream=bool(stream))
        self._api_key = clean_api_key
        self._allowed_emotions = (
            tuple(str(item) for item in allowed_emotions)
            if allowed_emotions is not None
            else None
        )
        self._allowed_actions = (
            tuple(str(item) for item in allowed_actions)
            if allowed_actions is not None
            else None
        )
        self._active = True
        try:
            reply = self._manager.post(request, QByteArray(request_data))
        except Exception as exc:
            self._finish_failure(f"无法启动聊天请求：{exc}")
            return
        self._reply = reply
        reply.readyRead.connect(lambda bound=reply: self._on_ready_read(bound))
        reply.finished.connect(lambda bound=reply: self._on_reply_finished(bound))
        self._timeout_timer.start(max(1, round(clean_timeout * 1000)))

    def cancel(self) -> None:
        """立即 abort 当前 reply，并以“已取消”结束本次请求。"""

        if self._active:
            self._abort_and_fail("聊天请求已取消")

    def _reset_buffers(self, *, stream: bool) -> None:
        self._stream = stream
        self._raw = bytearray()
        self._received_bytes = 0
        self._sse_parser = _IncrementalSseParser() if stream else None
        self._sse_text = []
        self._sse_text_chars = 0
        self._visible = _VisibleDeltaBuffer(self.delta.emit) if stream else None
        self._saw_done = False

    def _on_ready_read(self, reply: QNetworkReply) -> None:
        if not self._active or reply is not self._reply:
            return
        chunk = bytes(reply.readAll())
        if not chunk:
            return
        self._received_bytes += len(chunk)
        if self._received_bytes > _MAX_RESPONSE_BYTES:
            self._abort_and_fail("聊天服务响应超过大小上限")
            return
        self._raw.extend(chunk)
        if not self._stream or self._http_status(reply) >= 400:
            return
        parser = self._sse_parser
        if parser is None:
            return
        try:
            self._consume_sse_events(parser.feed(chunk))
        except ChatError as exc:
            self._abort_and_fail(str(exc))
        except Exception as exc:
            self._abort_and_fail(f"聊天服务 SSE 解析失败：{exc}")

    def _consume_sse_events(self, events: Iterable[str]) -> None:
        for data in events:
            if not self._active or self._saw_done:
                return
            event = parse_sse_event(data, secrets=(self._api_key,))
            if event.done:
                self._saw_done = True
                self._complete_sse_success()
                return
            if not event.delta:
                continue
            self._sse_text_chars += len(event.delta)
            if self._sse_text_chars > _MAX_STREAM_TEXT_CHARS:
                raise ChatProtocolError("聊天服务的流式文本超过大小上限")
            self._sse_text.append(event.delta)
            if self._visible is not None:
                self._visible.feed(event.delta)

    def _on_reply_finished(self, reply: QNetworkReply) -> None:
        if not self._active or reply is not self._reply:
            return
        self._on_ready_read(reply)
        if not self._active:
            return
        status = self._http_status(reply)
        if 300 <= status < 400:
            self._finish_failure(
                "聊天服务拒绝自动重定向；"
                "请直接填写最终 Chat Completions 地址"
            )
            return
        if status >= 400:
            detail = self._http_error_detail()
            self._finish_failure(
                f"聊天服务返回 HTTP {status}：{detail}"
            )
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._finish_failure(
                f"无法连接聊天服务：{reply.errorString()}"
            )
            return
        try:
            if self._stream:
                parser = self._sse_parser
                if parser is not None:
                    self._consume_sse_events(parser.feed(b"", final=True))
                if not self._active:
                    return
                if not self._saw_done:
                    raise ChatProtocolError("聊天服务的 SSE 响应意外结束")
                return
            content = _parse_json_response(
                bytes(self._raw),
                secrets=(self._api_key,),
            )
            result = parse_assistant_response(
                content,
                allowed_emotions=self._allowed_emotions,
                allowed_actions=self._allowed_actions,
            )
        except ChatError as exc:
            self._finish_failure(str(exc))
            return
        except Exception as exc:
            self._finish_failure(f"聊天服务响应解析失败：{exc}")
            return
        self._finish_success(result)

    def _complete_sse_success(self) -> None:
        if not self._active:
            return
        try:
            if self._visible is not None:
                self._visible.finish()
            content = "".join(self._sse_text)
            if not content:
                raise ChatProtocolError("聊天服务返回了空的 SSE 文本")
            result = parse_assistant_response(
                content,
                allowed_emotions=self._allowed_emotions,
                allowed_actions=self._allowed_actions,
            )
        except ChatError as exc:
            self._abort_and_fail(str(exc))
            return
        except Exception as exc:
            self._abort_and_fail(f"聊天服务 SSE 解析失败：{exc}")
            return
        self._finish_success(result, abort_reply=True)

    def _on_timeout(self) -> None:
        if self._active:
            self._abort_and_fail("聊天请求超时")

    def _abort_and_fail(self, message: str) -> None:
        self._finish_failure(message, abort_reply=True)

    def _finish_success(
        self,
        result: ChatReply,
        *,
        abort_reply: bool = False,
    ) -> None:
        if not self._active:
            return
        reply = self._detach_reply()
        if abort_reply and reply is not None:
            reply.abort()
        if reply is not None:
            reply.deleteLater()
        self._clear_sensitive_state()
        self.succeeded.emit(result)
        self.finished.emit()

    def _finish_failure(
        self,
        message: str,
        *,
        abort_reply: bool = False,
    ) -> None:
        if not self._active:
            return
        safe_message = sanitize_error_message(
            message,
            secrets=(self._api_key,),
        )
        reply = self._detach_reply()
        if abort_reply and reply is not None:
            reply.abort()
        if reply is not None:
            reply.deleteLater()
        self._clear_sensitive_state()
        self.failed.emit(safe_message)
        self.finished.emit()

    def _detach_reply(self) -> QNetworkReply | None:
        self._active = False
        self._timeout_timer.stop()
        reply = self._reply
        self._reply = None
        return reply

    def _clear_sensitive_state(self) -> None:
        self._api_key = ""
        self._allowed_emotions = None
        self._allowed_actions = None
        self._raw.clear()
        self._sse_text.clear()
        self._sse_parser = None
        self._visible = None

    @staticmethod
    def _http_status(reply: QNetworkReply) -> int:
        value = reply.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute
        )
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _http_error_detail(self) -> str:
        raw = bytes(self._raw[:_MAX_ERROR_BODY_BYTES])
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail: object = raw.decode("utf-8", errors="replace")
        else:
            detail = _extract_service_error(payload)
        return sanitize_error_message(detail, secrets=(self._api_key,))


__all__ = ["QtChatRequest"]
