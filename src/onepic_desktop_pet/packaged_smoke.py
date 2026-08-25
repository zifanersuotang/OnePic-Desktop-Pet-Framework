"""
本模块在源码或 PyInstaller 成品入口内执行真实窗口双击快捷聊天烟测。

它创建正式 DesktopPetApplication，在 PetWindow 中注入真实鼠标双击，确认只打开
单标签紧凑聊天窗、没有自动发起聊天请求，并保存 JSON 证据与截图。该入口专供发布
流水线验证成品依赖和 Qt 资源是否完整；报告同时绑定当前 EXE SHA-256、pet-id 与
发布准备批次，不替代用户最终视觉验收。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from .app import DesktopPetApplication
from .resources import resource_path


def _file_sha256(path: Path) -> str:
    """流式计算当前可执行进程映像的 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def distribution_tree_sha256(root: Path) -> str:
    """按相对路径、大小与内容计算完整 one-dir 成品树指纹。"""

    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(str(path.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _rendering_ready(manifest: dict[str, Any], live2d_ready: bool) -> bool:
    """精灵路线立即可测，混合路线等待同一窗口的 WebGL 就绪。"""

    mode = str(manifest.get("rendering_mode", "sprite")).strip()
    hybrid = mode == "hybrid-live2d" or isinstance(
        manifest.get("hybrid_runtime"),
        dict,
    )
    return bool(live2d_ready) if hybrid else True


def run_double_click_chat_smoke(
    output: Path,
    capture: Path,
    *,
    expected_pet_id: str,
    timeout_ms: int = 35_000,
) -> int:
    """运行可由打包 EXE 直接调用的双击聊天与零自动请求烟测。"""

    output = output.resolve()
    capture = capture.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    capture.parent.mkdir(parents=True, exist_ok=True)
    desktop = DesktopPetApplication()
    window = desktop.window
    controller = desktop.companion
    manifest_path = window._active_manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    expected_manifest_path = resource_path(
        "user_assets/pet/manifest.json"
    ).resolve()
    pointer_path = resource_path("user_assets/active-pet.json").resolve()
    pointer = json.loads(pointer_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or not isinstance(pointer, dict):
        raise TypeError("成品 manifest 与 active-pet.json 必须是 JSON 对象")
    executable = Path(sys.executable).resolve()
    result: dict[str, Any] = {
        "schema_version": 1,
        "finished": False,
        "timed_out": False,
        "manifest": str(manifest_path),
        "expected_manifest": str(expected_manifest_path),
        "manifest_is_packaged_customer_runtime": (
            manifest_path == expected_manifest_path
        ),
        "expected_pet_id": expected_pet_id,
        "actual_pet_id": manifest.get("pet_id"),
        "build_id": manifest.get("build_id"),
        "runtime_manifest_sha256": _file_sha256(manifest_path),
        "preparation_id": pointer.get("preparation_id"),
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable_sha256": _file_sha256(executable),
        "distribution_sha256": distribution_tree_sha256(executable.parent),
        "rendering_mode": manifest.get("rendering_mode", "sprite"),
    }

    if controller is None:
        result.update(
            {
                "status": "failed",
                "failure": "packaged companion controller unavailable",
            }
        )
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        desktop.quit()
        desktop.qt_app.processEvents()
        return 1

    def finish(*, timed_out: bool = False) -> None:
        if result["finished"]:
            return
        result["finished"] = True
        result["timed_out"] = timed_out
        desktop.quit()

    def inspect_dialog() -> None:
        compact = controller._compact_chat
        result.update(
            {
                "rendering_ready": _rendering_ready(
                    manifest,
                    window._live2d_ready,
                ),
                "live2d_ready": window._live2d_ready,
                "compact_created": compact is not None,
                "compact_visible": bool(
                    compact is not None and compact.isVisible()
                ),
                "compact_title": compact.windowTitle() if compact else None,
                "compact_tab_count": compact.tabs.count() if compact else 0,
                "compact_tab_label": (
                    compact.tabs.tabText(0) if compact else None
                ),
                "full_center_created": controller._center is not None,
                "chat_request_active": bool(
                    compact is not None and compact._chat_request.active
                ),
                "capture_saved": bool(
                    compact is not None
                    and compact.grab().save(str(capture), "PNG")
                ),
            }
        )
        finish()

    def perform_double_click() -> None:
        global_center = window.mapToGlobal(window.rect().center())
        widget = QApplication.widgetAt(global_center)
        result["widget_at_click"] = (
            type(widget).__name__ if widget is not None else None
        )
        QTest.mouseDClick(
            window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            window.rect().center(),
            5,
        )
        QTimer.singleShot(350, inspect_dialog)

    def poll_ready() -> None:
        if _rendering_ready(manifest, window._live2d_ready):
            QTimer.singleShot(150, perform_double_click)
        else:
            QTimer.singleShot(100, poll_ready)

    window.place_at_start()
    desktop.show_window()
    QTimer.singleShot(100, poll_ready)
    QTimer.singleShot(max(1, timeout_ms), lambda: finish(timed_out=True))
    desktop.qt_app.exec()
    passed = bool(
        not result.get("timed_out")
        and result.get("manifest_is_packaged_customer_runtime")
        and result.get("actual_pet_id") == expected_pet_id
        and result.get("rendering_ready")
        and result.get("widget_at_click") == "PetWindow"
        and result.get("compact_created")
        and result.get("compact_visible")
        and result.get("compact_title") == "OnePic 快捷聊天"
        and result.get("compact_tab_count") == 1
        and result.get("compact_tab_label") == "聊天"
        and not result.get("full_center_created")
        and not result.get("chat_request_active")
        and result.get("capture_saved")
    )
    report = {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "capture": str(capture),
        **result,
    }
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if passed else 1
