"""提供可回退的本地 Live2D WebGL、固定外观参数与常驻配件视图。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QTimer, Qt, QUrl, QUrlQuery, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from .live2d_profile import (
    normalize_live2d_idle_animation,
    normalize_live2d_persistent_parameters,
)
from .resources import resource_path


_READY_POLL_INTERVAL_MS = 120
_DEFAULT_READY_TIMEOUT_MS = 30_000
_MIN_READY_TIMEOUT_MS = 5_000
_MAX_READY_TIMEOUT_MS = 120_000


@dataclass(frozen=True)
class Live2DFiles:
    """经过边界校验的混合运行时文件。"""

    model: Path
    blink_overlay: Path | None
    layer_manifest: Path | None
    accessory_manifest: Path | None


class _Live2DHttpServer:
    """仅在 127.0.0.1 暴露宿主和当前宠物运行目录的只读文件。"""

    def __init__(self, pet_root: Path, host_root: Path) -> None:
        self.requests: list[str] = []
        request_log = self.requests
        roots = {
            "pet": pet_root.resolve(),
            "host": host_root.resolve(),
        }

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def translate_path(self, path: str) -> str:
                request_path = unquote(urlsplit(path).path)
                parts = [part for part in request_path.split("/") if part]
                if not parts or parts[0] not in roots:
                    return str(host_root / "__not_found__")
                root = roots[parts[0]]
                target = (root / Path(*parts[1:])).resolve()
                if target != root and root not in target.parents:
                    return str(root / "__not_found__")
                return str(target)

            def do_GET(self) -> None:
                request_log.append(urlsplit(self.path).path)
                super().do_GET()

            def do_HEAD(self) -> None:
                request_log.append(f"HEAD {urlsplit(self.path).path}")
                super().do_HEAD()

            def end_headers(self) -> None:
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                super().end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="onepic-live2d-http",
            daemon=True,
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _DiagnosticPage(QWebEnginePage):
    """保存最近的 JS 控制台消息，供烟测报告读取，不写用户目录。"""

    def __init__(self, profile: QWebEngineProfile, parent=None) -> None:
        super().__init__(profile, parent)
        self.console_messages: list[str] = []

    def javaScriptConsoleMessage(
        self,
        _level,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        self.console_messages.append(
            f"{source_id}:{line_number}: {message}"
        )
        del self.console_messages[:-20]


def _contained_file(root: Path, relative: object, label: str) -> Path:
    value = str(relative or "").strip()
    if not value:
        raise ValueError(f"Live2D {label} 未配置")
    path = (root / value).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"Live2D {label} 不能离开宠物运行时目录")
    if not path.is_file():
        raise FileNotFoundError(f"缺少 Live2D {label}：{path}")
    return path


def resolve_live2d_files(
    manifest_path: Path,
    config: dict[str, object],
) -> Live2DFiles:
    """解析模型、眨眼、分层和 Cubism 常驻配件，并阻止路径越界。"""

    root = manifest_path.resolve().parent
    model = _contained_file(root, config.get("model"), "模型")
    blink_value = config.get("blink_overlay")
    blink = (
        _contained_file(root, blink_value, "眨眼叠层")
        if str(blink_value or "").strip()
        else None
    )
    layer_manifest_value = config.get("layer_manifest")
    layer_manifest = (
        _contained_file(root, layer_manifest_value, "分层清单")
        if str(layer_manifest_value or "").strip()
        else None
    )
    accessory_manifest_value = config.get("accessory_manifest")
    accessory_manifest = (
        _contained_file(root, accessory_manifest_value, "常驻配件清单")
        if str(accessory_manifest_value or "").strip()
        else None
    )
    return Live2DFiles(
        model=model,
        blink_overlay=blink,
        layer_manifest=layer_manifest,
        accessory_manifest=accessory_manifest,
    )


class Live2DIdleView(QWebEngineView):
    """透明、鼠标穿透的 Live2D 待机视图；加载失败时发出回退信号。"""

    ready_changed = Signal(bool)

    def __init__(
        self,
        manifest_path: Path,
        config: dict[str, object],
        parent=None,
    ) -> None:
        super().__init__(parent)
        files = resolve_live2d_files(manifest_path, config)
        self._ready = False
        self._last_error = ""
        self._last_stage = ""
        self._animation_profile: dict[str, object] = {}
        self._supported_parameters: dict[str, object] = {}
        self._accessory_status: dict[str, object] = {}
        self._pending_presentation: dict[str, object] = {}
        self._pending_presentation_frame = 0
        self._last_presentation: dict[str, object] = {}
        self._poll_attempts = 0
        timeout_value = config.get(
            "load_timeout_ms", _DEFAULT_READY_TIMEOUT_MS
        )
        if isinstance(timeout_value, bool) or not isinstance(
            timeout_value, (int, float)
        ):
            timeout_value = _DEFAULT_READY_TIMEOUT_MS
        self._ready_timeout_ms = max(
            _MIN_READY_TIMEOUT_MS,
            min(_MAX_READY_TIMEOUT_MS, int(timeout_value)),
        )
        self._ready_poll_max_attempts = (
            self._ready_timeout_ms + 5_000
        ) // _READY_POLL_INTERVAL_MS
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_READY_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_ready_state)
        host_root = resource_path("assets/live2d_web")
        self._http_server = _Live2DHttpServer(
            manifest_path.resolve().parent,
            host_root,
        )

        self._profile = QWebEngineProfile(self)
        self._page = _DiagnosticPage(self._profile, self)
        self.setPage(self._page)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setStyleSheet("background: transparent;")
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        settings = self.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
            True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            False,
        )

        url = QUrl(f"{self._http_server.base_url}/host/index.html")
        query = QUrlQuery()
        model_relative = files.model.relative_to(manifest_path.resolve().parent)
        query.addQueryItem(
            "model",
            f"{self._http_server.base_url}/pet/{model_relative.as_posix()}",
        )
        if files.blink_overlay is not None:
            blink_relative = files.blink_overlay.relative_to(
                manifest_path.resolve().parent
            )
            query.addQueryItem(
                "blink",
                f"{self._http_server.base_url}/pet/{blink_relative.as_posix()}",
            )
        if files.layer_manifest is not None:
            layer_relative = files.layer_manifest.relative_to(
                manifest_path.resolve().parent
            )
            query.addQueryItem(
                "layers",
                f"{self._http_server.base_url}/pet/{layer_relative.as_posix()}",
            )
        if files.accessory_manifest is not None:
            accessory_relative = files.accessory_manifest.relative_to(
                manifest_path.resolve().parent
            )
            query.addQueryItem(
                "accessories",
                f"{self._http_server.base_url}/pet/"
                f"{accessory_relative.as_posix()}",
            )
        animation_profile = normalize_live2d_idle_animation(
            config.get("idle_animation")
        )
        query.addQueryItem(
            "animation",
            json.dumps(
                animation_profile,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        persistent_parameters = normalize_live2d_persistent_parameters(
            config.get("persistent_parameters")
        )
        query.addQueryItem(
            "persistent_parameters",
            json.dumps(
                persistent_parameters,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        query.addQueryItem("load_timeout_ms", str(self._ready_timeout_ms))
        url.setQuery(query)
        self.loadFinished.connect(self._page_loaded)
        self.destroyed.connect(lambda: self._http_server.close())
        self.load(url)
        self.hide()

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def diagnostics(self) -> dict[str, object]:
        return {
            "ready": self._ready,
            "last_error": self._last_error,
            "last_stage": self._last_stage,
            "animation_profile": dict(self._animation_profile),
            "supported_parameters": dict(self._supported_parameters),
            "accessory_status": dict(self._accessory_status),
            "last_presentation": dict(self._last_presentation),
            "requests": list(self._http_server.requests),
            "console": list(self._page.console_messages),
        }

    def _page_loaded(self, ok: bool) -> None:
        if not ok:
            self._last_error = "Live2D 宿主页加载失败"
            self.ready_changed.emit(False)
            return
        self._poll_attempts = 0
        self._poll_timer.start()

    def _poll_ready_state(self) -> None:
        self._poll_attempts += 1
        script = (
            "JSON.stringify({"
            "ready: document.documentElement.dataset.live2dReady === 'true',"
            " error: document.documentElement.dataset.live2dError || '',"
            " stage: document.documentElement.dataset.live2dStage || '',"
            " renderer:"
            " document.documentElement.dataset.live2dRenderer || '',"
            " profile: document.documentElement.dataset.live2dProfile || '{}',"
            " parameters:"
            " document.documentElement.dataset.live2dParameters || '{}',"
            " accessories:"
            " window.onepicLive2D?.getStatus?.().accessories ||"
            " document.documentElement.dataset.live2dAccessories || '{}'})"
        )
        self.page().runJavaScript(script, self._accept_ready_state)

    def _accept_ready_state(self, result: object) -> None:
        if isinstance(result, str):
            try:
                decoded = json.loads(result)
            except json.JSONDecodeError:
                decoded = {}
            state = decoded if isinstance(decoded, dict) else {}
        else:
            state = result if isinstance(result, dict) else {}
        self._last_stage = str(state.get("stage") or self._last_stage)
        for state_key, attribute in (
            ("profile", "_animation_profile"),
            ("parameters", "_supported_parameters"),
            ("accessories", "_accessory_status"),
        ):
            raw_value = state.get(state_key)
            if isinstance(raw_value, dict):
                setattr(self, attribute, raw_value)
                continue
            if isinstance(raw_value, str):
                try:
                    parsed_value = json.loads(raw_value)
                except json.JSONDecodeError:
                    parsed_value = {}
                if isinstance(parsed_value, dict):
                    setattr(self, attribute, parsed_value)
        renderer = str(state.get("renderer") or "")
        if renderer:
            self._animation_profile["renderer"] = renderer
        if state.get("ready"):
            self._poll_timer.stop()
            self._ready = True
            self.ready_changed.emit(True)
            self.apply_presentation(
                self._pending_presentation,
                frame_index=self._pending_presentation_frame,
            )
            return
        if (
            state.get("error")
            or self._poll_attempts >= self._ready_poll_max_attempts
        ):
            self._poll_timer.stop()
            self._ready = False
            self._last_error = str(
                state.get("error") or "Live2D 初始化等待超时"
            )
            self.ready_changed.emit(False)

    def apply_presentation(
        self,
        presentation: dict[str, object],
        *,
        frame_index: int = 0,
    ) -> None:
        """把已验证状态映射到模型；未就绪时只保留最后一次请求。"""

        spec = dict(presentation)
        expressions = spec.get("expressions")
        if isinstance(expressions, list) and expressions:
            spec["expression"] = str(
                expressions[max(0, int(frame_index)) % len(expressions)]
            )
        spec.pop("expressions", None)
        self._pending_presentation = dict(presentation)
        self._pending_presentation_frame = max(0, int(frame_index))
        self._last_presentation = spec
        if not self._ready:
            return
        payload = json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
        script = (
            "(() => {"
            "const api=window.onepicLive2D;"
            "if(!api){return false;}"
            f"const spec={payload};"
            "api.setPaused(false);"
            "api.neutralize();"
            "api.setPaused(Boolean(spec.paused));"
            "if(spec.parameters){api.setParameters(spec.parameters);}"
            "if(spec.expression){api.setExpression(spec.expression);}"
            "if(spec.motion){api.startMotion("
            "spec.motion.group,spec.motion.no,spec.motion.priority);}"
            "return true;"
            "})()"
        )
        self.page().runJavaScript(script)
