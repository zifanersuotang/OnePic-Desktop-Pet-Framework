"""在真实 PetWindow 上双击并验证紧凑聊天窗端到端打开且不联网。

精灵路线在窗口显示后即可测试；只有混合 Live2D 路线需要等待 WebGL 就绪，
避免把纯精灵宠物误判为加载超时。JSON 报告记录实际 pet-id，供发布终审绑定。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.companion.secrets import ApiKeyStore
from onepic_desktop_pet.companion.storage import CompanionStorage
from onepic_desktop_pet.companion.ui import CompanionController
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.window import PetWindow


def _file_sha256(path: Path) -> str:
    """计算被测 runtime manifest 的 SHA-256。"""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def rendering_is_ready(manifest: dict[str, object], live2d_ready: bool) -> bool:
    """按显式渲染路线判断窗口是否可进行双击测试。"""

    mode = str(manifest.get("rendering_mode", "sprite")).strip()
    hybrid = mode == "hybrid-live2d" or isinstance(
        manifest.get("hybrid_runtime"),
        dict,
    )
    return bool(live2d_ready) if hybrid else True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--display-height", type=int, default=420)
    parser.add_argument("--timeout-ms", type=int, default=35_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    os.environ["ONEPIC_PET_MANIFEST"] = str(manifest_path)
    os.environ.pop("ONEPIC_USE_DEMO_ASSETS", None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.capture.parent.mkdir(parents=True, exist_ok=True)
    data_root = args.output.parent / "companion-data"
    data_root.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)
    window = PetWindow(PetSettings(display_height=args.display_height))
    controller = CompanionController(
        window,
        storage=CompanionStorage(data_root / "companion.sqlite3"),
        key_store=ApiKeyStore(data_root / "api-key.dpapi"),
    )
    window.attach_companion_controller(controller)
    window.place_at_start()
    window.show()
    result: dict[str, object] = {"finished": False}

    def finish() -> None:
        if result["finished"]:
            return
        result["finished"] = True
        app.quit()

    def inspect_dialog() -> None:
        compact = controller._compact_chat
        ready = rendering_is_ready(manifest, window._live2d_ready)
        result.update(
            {
                "rendering_mode": manifest.get("rendering_mode", "sprite"),
                "rendering_ready": ready,
                "live2d_ready": window._live2d_ready,
                "compact_created": compact is not None,
                "compact_visible": bool(compact is not None and compact.isVisible()),
                "compact_title": compact.windowTitle() if compact else None,
                "compact_tab_count": compact.tabs.count() if compact else 0,
                "compact_tab_label": compact.tabs.tabText(0) if compact else None,
                "full_center_created": controller._center is not None,
                "chat_request_active": bool(
                    compact is not None and compact._chat_request.active
                ),
                "capture_saved": bool(
                    compact is not None
                    and compact.grab().save(str(args.capture), "PNG")
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
        if rendering_is_ready(manifest, window._live2d_ready):
            QTimer.singleShot(150, perform_double_click)
            return
        QTimer.singleShot(100, poll_ready)

    QTimer.singleShot(100, poll_ready)
    QTimer.singleShot(args.timeout_ms, finish)
    app.exec()
    passed = bool(
        result.get("rendering_ready")
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
        "pet_id": manifest.get("pet_id"),
        "build_id": manifest.get("build_id"),
        "runtime_manifest_sha256": _file_sha256(manifest_path),
        "manifest": str(manifest_path),
        "display_height": args.display_height,
        "capture": str(args.capture.resolve()),
        **result,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    controller.shutdown()
    window.close()
    window.deleteLater()
    app.processEvents()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
