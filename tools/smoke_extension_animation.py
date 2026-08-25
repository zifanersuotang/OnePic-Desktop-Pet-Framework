"""在真实 PetWindow 中逐帧验收一个扩展动画。

职责范围：
- 从当前运行清单加载指定扩展；
- 逐帧强制显示并抓取桌宠窗口；
- 检查帧数、透明像素、窗口裁切和扩展状态；
- 输出联系表与 JSON 报告供人工视觉复核。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.window import PetWindow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--extension", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--contact-sheet", type=Path, required=True)
    parser.add_argument("--display-height", type=int, default=360)
    parser.add_argument("--timeout-ms", type=int, default=15_000)
    return parser.parse_args()


def make_contact_sheet(paths: list[Path], destination: Path) -> None:
    images = [Image.open(path).convert("RGBA") for path in paths]
    cell_width = max(image.width for image in images)
    cell_height = max(image.height for image in images) + 22
    columns = min(4, len(images))
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), "#eeeeee")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, image in enumerate(images):
        x0 = (index % columns) * cell_width
        y0 = (index // columns) * cell_height
        draw.rectangle((x0, y0, x0 + cell_width, y0 + 22), fill="#222222")
        draw.text((x0 + 6, y0 + 5), f"frame {index + 1}", fill="white", font=font)
        checker = Image.new("RGBA", (cell_width, cell_height - 22), "white")
        checker_draw = ImageDraw.Draw(checker)
        block = 18
        for y in range(0, checker.height, block):
            for x in range(0, checker.width, block):
                if (x // block + y // block) % 2:
                    checker_draw.rectangle(
                        (x, y, x + block - 1, y + block - 1),
                        fill="#d7d7d7",
                    )
        checker.alpha_composite(
            image,
            (
                (checker.width - image.width) // 2,
                (checker.height - image.height) // 2,
            ),
        )
        sheet.paste(checker.convert("RGB"), (x0, y0 + 22))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, "PNG", optimize=True)


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    extension = manifest.get("extension_animations", {}).get(args.extension)
    if not isinstance(extension, dict):
        raise ValueError(f"运行清单中不存在扩展：{args.extension}")
    expected_frames = extension.get("frames", [])
    if not isinstance(expected_frames, list) or not expected_frames:
        raise ValueError("扩展动画没有可验收帧")

    os.environ["ONEPIC_PET_MANIFEST"] = str(manifest_path)
    os.environ.pop("ONEPIC_USE_DEMO_ASSETS", None)
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)
    window = PetWindow(PetSettings(display_height=args.display_height))
    window.place_at_start()
    window.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    window.show()
    state: dict[str, object] = {
        "finished": False,
        "success": False,
        "captures": [],
    }

    def finish(success: bool) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        state["success"] = success
        app.quit()

    def capture(index: int) -> None:
        if index >= len(expected_frames):
            finish(True)
            return
        if window._extension_active_name != args.extension:
            finish(False)
            return
        window._extension_frame_index = index
        window._refresh_pixmap()
        path = args.capture_dir / f"{index + 1:02d}.png"

        def save() -> None:
            if not window.grab().save(str(path), "PNG"):
                finish(False)
                return
            state["captures"].append(path)
            QTimer.singleShot(50, lambda: capture(index + 1))

        QTimer.singleShot(80, save)

    def start() -> None:
        if not window.trigger_extension_action(args.extension):
            finish(False)
            return
        # 冒烟测试逐帧手动指定索引；停止自动计时器，避免截图保存耗时
        # 跨过 frame_duration_ms 后把末尾两帧推进成同一张。
        window.extension_timer.stop()
        capture(0)

    QTimer.singleShot(250, start)
    QTimer.singleShot(args.timeout_ms, lambda: finish(False))
    app.exec()

    capture_records: list[dict[str, object]] = []
    for path in state["captures"]:
        with Image.open(path).convert("RGBA") as image:
            alpha = image.getchannel("A")
            bbox = alpha.getbbox()
            nontransparent = image.width * image.height - alpha.histogram()[0]
            capture_records.append(
                {
                    "path": str(path.resolve()),
                    "size": list(image.size),
                    "alpha_bbox": list(bbox) if bbox else None,
                    "nontransparent_pixels": nontransparent,
                }
            )
    if capture_records:
        make_contact_sheet(list(state["captures"]), args.contact_sheet)

    report = {
        "status": (
            "passed"
            if state["success"]
            and len(capture_records) == len(expected_frames)
            and all(item["nontransparent_pixels"] >= 500 for item in capture_records)
            and window._extension_active_name == args.extension
            else "failed"
        ),
        "manifest": str(manifest_path),
        "extension": args.extension,
        "expected_frame_count": len(expected_frames),
        "captured_frame_count": len(capture_records),
        "display_height": args.display_height,
        "captures": capture_records,
        "contact_sheet": str(args.contact_sheet.resolve()),
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    window.close()
    window.deleteLater()
    app.processEvents()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
