"""在真实 PetWindow 中逐项验证 Live2D 状态映射、尺度稳定性和透明合成。"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.window import PetWindow


BASE_STATE_ORDER = (
    "idle",
    "walk",
    "sit",
    "sleep",
    "drag",
    "selfie",
    "wave",
    "happy",
    "shy",
    "surprised",
    "annoyed",
    "sleepy",
    "curious",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--display-height", type=int, default=420)
    parser.add_argument("--timeout-ms", type=int, default=60_000)
    return parser.parse_args()


def _alpha_metrics(path: Path) -> tuple[list[int] | None, int]:
    with Image.open(path).convert("RGBA") as image:
        alpha = image.getchannel("A")
        bbox = alpha.getbbox()
        visible = image.width * image.height - alpha.histogram()[0]
    return (list(bbox) if bbox else None, visible)


def _contact_sheet(paths: list[tuple[str, Path]], target: Path) -> None:
    columns = 6
    cell_width = 230
    cell_height = 230
    rows = math.ceil(len(paths) / columns)
    sheet = Image.new(
        "RGB",
        (columns * cell_width, rows * cell_height),
        (245, 241, 247),
    )
    draw = ImageDraw.Draw(sheet)
    for index, (label, path) in enumerate(paths):
        with Image.open(path).convert("RGBA") as source:
            source.thumbnail((cell_width - 12, cell_height - 34), Image.Resampling.LANCZOS)
            checker = Image.new("RGBA", source.size, (255, 255, 255, 255))
            checker.alpha_composite(source)
        column = index % columns
        row = index // columns
        x = column * cell_width + (cell_width - checker.width) // 2
        y = row * cell_height + 24 + (cell_height - 28 - checker.height) // 2
        sheet.paste(checker.convert("RGB"), (x, y))
        draw.text((column * cell_width + 8, row * cell_height + 6), label, fill=(45, 37, 56))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, "PNG", optimize=True)


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    state_map = manifest.get("hybrid_runtime", {}).get("live2d_state_map", {})
    if not isinstance(state_map, dict) or not state_map:
        raise ValueError("运行清单没有 live2d_state_map")

    queue: list[tuple[str, str, int]] = []
    for state_name in BASE_STATE_ORDER:
        if state_name in state_map:
            queue.append((state_name, state_name, 0))
    for key, presentation in state_map.items():
        if not str(key).startswith("extension:") or not isinstance(presentation, dict):
            continue
        expression_count = len(presentation.get("expressions", [])) or 1
        for index in range(expression_count):
            queue.append((f"{key}[{index}]", str(key), index))

    os.environ["ONEPIC_PET_MANIFEST"] = str(manifest_path)
    os.environ.pop("ONEPIC_USE_DEMO_ASSETS", None)
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    window = PetWindow(PetSettings(display_height=args.display_height))
    window.place_at_start()
    window.show()
    records: list[dict[str, object]] = []
    captures: list[tuple[str, Path]] = []
    active_index = 0
    finished = False

    def finish() -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        app.quit()

    def capture_current(label: str, key: str, frame_index: int) -> None:
        nonlocal active_index
        safe_name = f"{active_index + 1:02d}-{label}".replace(":", "-").replace("[", "-").replace("]", "")
        path = args.capture_dir / f"{safe_name}.png"
        saved = window.grab().save(str(path), "PNG")
        view = window._live2d_view
        diagnostics = getattr(view, "diagnostics", {}) if view is not None else {}
        record = {
            "label": label,
            "mapping_key": key,
            "frame_index": frame_index,
            "saved": bool(saved),
            "live2d_visible": bool(view is not None and view.isVisible()),
            "sprite_visible": window.label.isVisible(),
            "presentation": diagnostics.get("last_presentation", {}),
            "capture": str(path.resolve()),
        }
        records.append(record)
        if saved:
            captures.append((label, path))
        active_index += 1
        QTimer.singleShot(25, apply_next)

    def apply_next() -> None:
        if active_index >= len(queue):
            finish()
            return
        label, key, frame_index = queue[active_index]
        window.state_timer.stop()
        window.movement_timer.stop()
        window.interaction_timer.stop()
        window.autoplay_timer.stop()
        if key.startswith("extension:"):
            name = key.split(":", 1)[1]
            if not window.play_extension_animation(name):
                records.append({"label": label, "error": "extension-start-failed"})
                finish()
                return
            window.extension_timer.stop()
            window._extension_frame_index = frame_index
            window._sync_hybrid_view(window.state)
        else:
            window.set_state(PetState(key))
            window.state_timer.stop()
            window.movement_timer.stop()
        QTimer.singleShot(
            460,
            lambda value=label, mapping=key, index=frame_index: capture_current(
                value,
                mapping,
                index,
            ),
        )

    def poll_ready() -> None:
        if window._live2d_ready:
            apply_next()
            return
        QTimer.singleShot(100, poll_ready)

    QTimer.singleShot(100, poll_ready)
    QTimer.singleShot(args.timeout_ms, finish)
    app.exec()

    for record in records:
        capture_value = record.get("capture")
        if not isinstance(capture_value, str):
            continue
        bbox, visible_pixels = _alpha_metrics(Path(capture_value))
        record["alpha_bbox"] = bbox
        record["visible_pixels"] = visible_pixels
        record["visible_height"] = bbox[3] - bbox[1] if bbox else 0

    base_heights = [
        int(record.get("visible_height", 0))
        for record in records
        if record.get("mapping_key") in BASE_STATE_ORDER
        and int(record.get("visible_height", 0)) > 0
    ]
    base_height_ratio = (
        max(base_heights) / min(base_heights) if base_heights else 0.0
    )
    sheet_path = args.capture_dir / "live2d-state-contact-sheet.png"
    if captures:
        _contact_sheet(captures, sheet_path)
    passed = bool(
        len(records) == len(queue)
        and all(record.get("saved") for record in records)
        and all(record.get("live2d_visible") for record in records)
        and all(not record.get("sprite_visible") for record in records)
        and all(int(record.get("visible_pixels", 0)) >= 500 for record in records)
        and base_height_ratio <= 1.08
    )
    report = {
        "status": "passed" if passed else "failed",
        "manifest": str(manifest_path),
        "display_height": args.display_height,
        "expected_capture_count": len(queue),
        "capture_count": len(records),
        "base_visible_height_ratio": round(base_height_ratio, 6),
        "contact_sheet": str(sheet_path.resolve()),
        "records": records,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    window.close()
    window.deleteLater()
    app.processEvents()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
