"""逐值捕获一个 Live2D 参数，补足仅检查最小/最大端点的视觉盲区。

职责范围：
- 关闭自动动画并暂停模型；
- 对指定参数按声明顺序写入多个值；
- 生成逐值透明截图、联系表和 JSON 报告；
- 不修改模型、运行清单或当前激活桌宠。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.live2d_view import Live2DIdleView


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--parameter", required=True)
    parser.add_argument("--values", nargs="+", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--contact-sheet", type=Path, required=True)
    parser.add_argument("--settle-ms", type=int, default=160)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    return parser.parse_args()


def make_contact_sheet(
    values: list[float],
    paths: list[Path],
    destination: Path,
) -> None:
    images = [Image.open(path).convert("RGBA") for path in paths]
    width = max(image.width for image in images)
    height = max(image.height for image in images)
    cell_width = width
    cell_height = height + 24
    columns = len(images)
    sheet = Image.new("RGBA", (columns * cell_width, cell_height), "#eeeeee")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (value, image) in enumerate(zip(values, images, strict=True)):
        x0 = index * cell_width
        draw.rectangle((x0, 0, x0 + cell_width, 24), fill="#222222")
        draw.text((x0 + 8, 6), f"value={value:g}", fill="white", font=font)
        checker = Image.new("RGBA", (cell_width, height), "white")
        checker_draw = ImageDraw.Draw(checker)
        block = 20
        for y in range(0, height, block):
            for x in range(0, cell_width, block):
                if (x // block + y // block) % 2:
                    checker_draw.rectangle(
                        (x, y, x + block - 1, y + block - 1),
                        fill="#d7d7d7",
                    )
        checker.alpha_composite(
            image,
            ((cell_width - image.width) // 2, (height - image.height) // 2),
        )
        sheet.alpha_composite(checker, (x0, 24))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(destination, "PNG", optimize=True)


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
        "finished": False,
        "success": False,
        "index": 0,
        "captures": [],
        "nontransparent_pixels": [],
        "capture_attempt": 0,
        "range": None,
    }

    def finish(success: bool) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        state["success"] = success
        app.quit()

    def capture_next() -> None:
        index = int(state["index"])
        if index >= len(args.values):
            finish(True)
            return
        value = args.values[index]
        script = (
            "window.onepicLive2D.neutralize();"
            "window.onepicLive2D.setParameters("
            + json.dumps(
                {
                    args.parameter: value,
                    "ParamEyeROpen": value,
                }
            )
            + ")"
        )

        def save(_result: object) -> None:
            path = args.capture_dir / f"{index + 1:02d}-{value:g}.png"

            def grab() -> None:
                if not view.grab().save(str(path), "PNG"):
                    finish(False)
                    return
                with Image.open(path).convert("RGBA") as captured:
                    alpha = captured.getchannel("A")
                    nontransparent = (
                        captured.width * captured.height - alpha.histogram()[0]
                    )
                if nontransparent < 500:
                    attempt = int(state["capture_attempt"]) + 1
                    state["capture_attempt"] = attempt
                    if attempt < 12:
                        QTimer.singleShot(120, grab)
                    else:
                        finish(False)
                    return
                state["capture_attempt"] = 0
                state["captures"].append(path)
                state["nontransparent_pixels"].append(nontransparent)
                state["index"] = index + 1
                QTimer.singleShot(0, capture_next)

            QTimer.singleShot(args.settle_ms, grab)

        view.page().runJavaScript(script, save)

    def accept_status(result: object) -> None:
        try:
            status = json.loads(result) if isinstance(result, str) else {}
        except json.JSONDecodeError:
            status = {}
        ranges = status.get("parameterRanges", {})
        parameter_range = ranges.get(args.parameter) if isinstance(ranges, dict) else None
        if not isinstance(parameter_range, dict):
            finish(False)
            return
        state["range"] = parameter_range
        minimum = float(parameter_range["min"])
        maximum = float(parameter_range["max"])
        if any(value < minimum or value > maximum for value in args.values):
            finish(False)
            return
        capture_next()

    def on_ready(ready: bool) -> None:
        if not ready:
            finish(False)
            return
        view.raise_()
        QTimer.singleShot(
            320,
            lambda: view.page().runJavaScript(
                "window.onepicLive2D.setPaused(true);"
                "JSON.stringify(window.onepicLive2D.getStatus())",
                accept_status,
            ),
        )

    view.ready_changed.connect(on_ready)
    QTimer.singleShot(args.timeout_ms, lambda: finish(False))
    app.exec()

    paths = list(state["captures"])
    if paths:
        make_contact_sheet(args.values, paths, args.contact_sheet)
    report = {
        "status": (
            "passed"
            if state["success"] and len(paths) == len(args.values)
            else "failed"
        ),
        "model": args.model,
        "parameter": args.parameter,
        "range": state["range"],
        "values": args.values,
        "captures": [str(path.resolve()) for path in paths],
        "nontransparent_pixels": state["nontransparent_pixels"],
        "contact_sheet": str(args.contact_sheet.resolve()),
        "diagnostics": view.diagnostics,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    view.close()
    view.deleteLater()
    app.processEvents()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
