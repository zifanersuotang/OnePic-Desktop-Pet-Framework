"""探测 Live2D 参数最小/最大值是否产生真实像素变形。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.live2d_view import Live2DIdleView


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--settle-ms", type=int, default=140)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    return parser.parse_args()


def compare_images(minimum_path: Path, maximum_path: Path) -> dict[str, object]:
    """返回两端点截图的像素变化范围。"""

    with Image.open(minimum_path).convert("RGBA") as minimum:
        with Image.open(maximum_path).convert("RGBA") as maximum:
            difference = ImageChops.difference(minimum, maximum)
            rgb_difference = difference.convert("RGB")
            grayscale = rgb_difference.convert("L")
            histogram = grayscale.histogram()
            changed_pixels = sum(histogram[3:])
            mean = ImageStat.Stat(rgb_difference).mean
            return {
                "changed_pixels": changed_pixels,
                "difference_bbox": grayscale.point(
                    lambda value: 255 if value > 2 else 0
                ).getbbox(),
                "mean_absolute_rgb": round(sum(mean) / 3, 6),
                "visually_bound": changed_pixels >= 24,
            }


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = args.runtime_root.resolve()
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv)
    view = Live2DIdleView(
        root / "runtime-manifest.json",
        {
            "model": args.model,
            "idle_animation": {
                "enabled": False,
                "root_motion": False,
            },
        },
    )
    view.resize(420, 500)
    view.show()

    state: dict[str, object] = {
        "done": False,
        "ranges": {},
        "parameters": [],
        "index": 0,
        "results": {},
    }

    def finish(success: bool) -> None:
        if state["done"]:
            return
        state["done"] = True
        state["success"] = success
        app.quit()

    def run_script(script: str, callback) -> None:
        view.page().runJavaScript(script, callback)

    def capture(path: Path, callback) -> None:
        saved = view.grab().save(str(path), "PNG")
        if not saved:
            finish(False)
            return
        callback()

    def capture_maximum(parameter: str, maximum_path: Path) -> None:
        def after_capture() -> None:
            minimum_path = args.capture_dir / f"{parameter}-min.png"
            state["results"][parameter] = {
                "range": state["ranges"][parameter],
                **compare_images(minimum_path, maximum_path),
            }
            state["index"] = int(state["index"]) + 1
            QTimer.singleShot(0, probe_next)

        capture(maximum_path, after_capture)

    def set_maximum(parameter: str) -> None:
        maximum = state["ranges"][parameter]["max"]
        maximum_path = args.capture_dir / f"{parameter}-max.png"
        script = (
            "window.onepicLive2D.setParameters("
            + json.dumps({parameter: maximum})
            + ")"
        )
        run_script(
            script,
            lambda _result: QTimer.singleShot(
                args.settle_ms,
                lambda: capture_maximum(parameter, maximum_path),
            ),
        )

    def capture_minimum(parameter: str) -> None:
        minimum_path = args.capture_dir / f"{parameter}-min.png"
        capture(
            minimum_path,
            lambda: QTimer.singleShot(0, lambda: set_maximum(parameter)),
        )

    def set_minimum(parameter: str) -> None:
        minimum = state["ranges"][parameter]["min"]
        script = (
            "window.onepicLive2D.neutralize();"
            "window.onepicLive2D.setParameters("
            + json.dumps({parameter: minimum})
            + ")"
        )
        run_script(
            script,
            lambda _result: QTimer.singleShot(
                args.settle_ms,
                lambda: capture_minimum(parameter),
            ),
        )

    def probe_next() -> None:
        parameters = state["parameters"]
        index = int(state["index"])
        if index >= len(parameters):
            finish(True)
            return
        set_minimum(str(parameters[index]))

    def accept_status(result: object) -> None:
        try:
            status = json.loads(result) if isinstance(result, str) else {}
        except json.JSONDecodeError:
            status = {}
        ranges = status.get("parameterRanges", {})
        if not isinstance(ranges, dict) or not ranges:
            finish(False)
            return
        state["ranges"] = ranges
        state["parameters"] = sorted(ranges)
        probe_next()

    def on_ready(ready: bool) -> None:
        if not ready:
            finish(False)
            return
        run_script(
            "window.onepicLive2D.setPaused(true);"
            "JSON.stringify(window.onepicLive2D.getStatus())",
            accept_status,
        )

    view.ready_changed.connect(on_ready)
    QTimer.singleShot(args.timeout_ms, lambda: finish(False))
    app.exec()
    diagnostics = view.diagnostics
    view.close()
    view.deleteLater()
    app.processEvents()

    report = {
        "status": "passed" if state.get("success") else "failed",
        "model": args.model,
        "parameter_count": len(state["parameters"]),
        "visually_bound_parameters": sorted(
            parameter
            for parameter, result in state["results"].items()
            if result["visually_bound"]
        ),
        "parameters": state["results"],
        "diagnostics": diagnostics,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if state.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
