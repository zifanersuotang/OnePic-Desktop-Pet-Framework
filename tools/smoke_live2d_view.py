"""验证本地 Live2D 待机与同模型网格绑定配件视图，并输出可审计诊断。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.live2d_view import Live2DIdleView


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--blink-overlay")
    parser.add_argument("--layer-manifest")
    parser.add_argument("--accessory-manifest")
    parser.add_argument(
        "--idle-animation-json",
        help="可选：已校验的 idle_animation JSON 对象",
    )
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--load-timeout-ms", type=int, default=30_000)
    parser.add_argument("--timeout-ms", type=int, default=20_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = args.runtime_root.resolve()
    QCoreApplication.setAttribute(
        Qt.ApplicationAttribute.AA_ShareOpenGLContexts
    )
    app = QApplication.instance() or QApplication(sys.argv)
    config: dict[str, object] = {
        "model": args.model,
        "load_timeout_ms": args.load_timeout_ms,
    }
    if args.blink_overlay:
        config["blink_overlay"] = args.blink_overlay
    if args.layer_manifest:
        config["layer_manifest"] = args.layer_manifest
    if args.accessory_manifest:
        config["accessory_manifest"] = args.accessory_manifest
    if args.idle_animation_json:
        animation = json.loads(args.idle_animation_json)
        if not isinstance(animation, dict):
            raise ValueError("--idle-animation-json 必须是 JSON 对象")
        config["idle_animation"] = animation
    view = Live2DIdleView(root / "runtime-manifest.json", config)
    view.resize(360, 420)
    view.show()

    result = {"completed": False}

    def finish(ready: bool) -> None:
        if result["completed"]:
            return
        result["completed"] = True
        result["ready"] = ready
        if not ready:
            QTimer.singleShot(0, app.quit)
            return

        def capture_and_quit() -> None:
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                result["screenshot_saved"] = view.grab().save(
                    str(args.screenshot), "PNG"
                )
                with Image.open(args.screenshot).convert("RGBA") as image:
                    alpha = image.getchannel("A")
                    result["nontransparent_bbox"] = alpha.getbbox()
                    result["nontransparent_pixels"] = sum(
                        alpha.histogram()[1:]
                    )
                if not result["nontransparent_bbox"]:
                    result["ready"] = False
            app.quit()

        QTimer.singleShot(700, capture_and_quit)

    view.ready_changed.connect(finish)
    QTimer.singleShot(args.timeout_ms, lambda: finish(False))
    app.exec()
    result["diagnostics"] = view.diagnostics
    print(json.dumps(result, ensure_ascii=False, indent=2))
    view.close()
    view.deleteLater()
    app.processEvents()
    return 0 if result.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
