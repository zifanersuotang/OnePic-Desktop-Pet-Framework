"""Build transparent mouse-look frames directly from a short green-screen video.

The tool deliberately keeps the source pixels.  It only selects frames, removes
the green screen, clears an optional overlay strip, and places every result on
the same transparent canvas.  A small manifest is emitted so the runtime can
load the result without copying private source video into public assets.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageChops, ImageDraw


DEFAULT_TIMESTAMPS = {
    "center": 0.00,
    "north": 0.72,
    "north_east": 2.20,
    "east": 2.92,
    "south_east": 3.66,
    "south": 4.42,
    "south_west": 5.82,
    "west": 6.57,
    "north_west": 7.32,
}

DIRECTION_ORDER = (
    "center",
    "north",
    "north_east",
    "east",
    "south_east",
    "south",
    "south_west",
    "west",
    "north_west",
)


def _key_green(
    source: Image.Image,
    *,
    clear_top: int,
    soft_start: int,
    soft_end: int,
    padding: int,
) -> Image.Image:
    rgb = source.convert("RGB")
    red, green, blue = rgb.split()
    strongest_non_green = ImageChops.lighter(red, blue)
    green_excess = ImageChops.subtract(green, strongest_non_green)
    alpha = green_excess.point(
        lambda value: (
            255
            if value <= soft_start
            else 0
            if value >= soft_end
            else round(255 * (soft_end - value) / (soft_end - soft_start))
        )
    )
    if clear_top > 0:
        alpha.paste(0, (0, 0, alpha.width, min(clear_top, alpha.height)))

    # Replace green spill on transition pixels with the strongest red/blue
    # channel. Fully opaque source pixels are left unchanged.
    clean_green = ImageChops.darker(green, strongest_non_green)
    edge_mask = ImageChops.invert(alpha)
    green = Image.composite(clean_green, green, edge_mask)
    keyed = Image.merge("RGBA", (red, green, blue, alpha))
    if padding <= 0:
        return keyed
    canvas = Image.new(
        "RGBA",
        (keyed.width + padding * 2, keyed.height + padding * 2),
        (0, 0, 0, 0),
    )
    canvas.alpha_composite(keyed, (padding, padding))
    return canvas


def _read_selected_frames(
    video_path: Path,
    timestamps: dict[str, float],
) -> tuple[dict[str, Image.Image], dict]:
    reader = imageio_ffmpeg.read_frames(str(video_path), pix_fmt="rgb24")
    metadata = next(reader)
    fps = float(metadata["fps"])
    width, height = metadata["size"]
    targets: dict[int, list[str]] = {}
    for direction, seconds in timestamps.items():
        index = max(0, round(seconds * fps))
        targets.setdefault(index, []).append(direction)
    last_target = max(targets)
    frames: dict[str, Image.Image] = {}
    try:
        for index, raw in enumerate(reader):
            directions = targets.get(index, ())
            for direction in directions:
                frames[direction] = Image.frombytes(
                    "RGB", (width, height), raw
                )
            if index >= last_target:
                break
    finally:
        reader.close()
    missing = sorted(set(timestamps) - set(frames))
    if missing:
        raise RuntimeError(f"Video ended before these directions: {missing}")
    return frames, metadata


def build(
    video_path: Path,
    output_root: Path,
    *,
    clear_top: int = 100,
    soft_start: int = 8,
    soft_end: int = 28,
    padding: int = 28,
) -> Path:
    timestamps = dict(DEFAULT_TIMESTAMPS)
    ring_count = 32
    ring_start = 0.72
    ring_duration = 7.28
    for index in range(ring_count):
        timestamps[f"ring_{index:02d}"] = (
            ring_start + ring_duration * index / ring_count
        )
    source_frames, metadata = _read_selected_frames(video_path, timestamps)
    frames_root = output_root / "directional"
    frames_root.mkdir(parents=True, exist_ok=True)

    # The source adds small platform overlays after the opening frame. Replace
    # only those fixed rectangles with clean pixels from the same original
    # video, keeping the person and desk entirely source-derived.
    clean_reference = source_frames["center"]
    overlay_rectangles = (
        (0, 92, 132, 132),
        (602, 650, clean_reference.width, clean_reference.height),
    )
    relative_frames: dict[str, str] = {}
    for direction in DIRECTION_ORDER:
        source = source_frames[direction].copy()
        for rectangle in overlay_rectangles:
            source.paste(clean_reference.crop(rectangle), rectangle)
        result = _key_green(
            source,
            clear_top=clear_top,
            soft_start=soft_start,
            soft_end=soft_end,
            padding=padding,
        )
        path = frames_root / f"{direction}.png"
        result.save(path, "PNG", optimize=True)
        relative_frames[direction] = path.relative_to(output_root).as_posix()

    ring_relative_frames: list[str] = []
    for index in range(ring_count):
        direction = f"ring_{index:02d}"
        source = source_frames[direction].copy()
        for rectangle in overlay_rectangles:
            source.paste(clean_reference.crop(rectangle), rectangle)
        result = _key_green(
            source,
            clear_top=clear_top,
            soft_start=soft_start,
            soft_end=soft_end,
            padding=padding,
        )
        path = frames_root / f"{direction}.png"
        result.save(path, "PNG", optimize=True)
        ring_relative_frames.append(path.relative_to(output_root).as_posix())

    center = relative_frames["center"]
    animations = {
        "wave": [center],
        "happy": [center],
        "idle": [center],
        "shy": [center],
        "surprised": [center],
        "annoyed": [center],
        "sleepy": [center],
        "curious": [center],
        "sit": [center],
        "sleep": [center],
        "walk": [center],
        "drag": [center],
        "selfie": [center],
    }
    manifest = {
        "sources": ["private original video; not packaged"],
        "canvas_size": [
            int(metadata["size"][0]) + padding * 2,
            int(metadata["size"][1]) + padding * 2,
        ],
        "stationary": True,
        "walk_motion_factors": [1.0],
        "animations": animations,
        "directional_mouse_follow": {
            "enabled": True,
            "frames": relative_frames,
            "ring_frames": ring_relative_frames,
            "poll_interval_ms": 33,
            "switch_delay_ms": 0,
            "dead_zone_px": 48,
        },
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _checkerboard(size: tuple[int, int], cell: int = 24) -> Image.Image:
    background = Image.new("RGBA", size, (238, 238, 238, 255))
    draw = ImageDraw.Draw(background)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle(
                    (x, y, min(x + cell, size[0]), min(y + cell, size[1])),
                    fill=(214, 214, 214, 255),
                )
    return background


def build_review(output_root: Path, review_path: Path) -> Path:
    """Create a deterministic cursor-sweep GIF for the interaction gate."""

    review_path.parent.mkdir(parents=True, exist_ok=True)
    contact_order = (
        "north_west",
        "north",
        "north_east",
        "west",
        "center",
        "east",
        "south_west",
        "south",
        "south_east",
    )
    contact_sheet = _checkerboard((1080, 1080), cell=30)
    contact_draw = ImageDraw.Draw(contact_sheet)
    for index, direction in enumerate(contact_order):
        source = Image.open(
            output_root / "directional" / f"{direction}.png"
        ).convert("RGBA")
        source.thumbnail((340, 340), Image.Resampling.LANCZOS)
        column = index % 3
        row = index // 3
        offset = (
            column * 360 + (360 - source.width) // 2,
            row * 360 + (360 - source.height) // 2,
        )
        contact_sheet.alpha_composite(source, offset)
        contact_draw.rounded_rectangle(
            (column * 360 + 10, row * 360 + 10, column * 360 + 170, row * 360 + 42),
            radius=7,
            fill=(0, 0, 0, 178),
        )
        contact_draw.text(
            (column * 360 + 20, row * 360 + 19),
            direction,
            fill="white",
        )
    contact_sheet.save(
        review_path.with_name("mouse-follow-direction-contact-sheet.png"),
        "PNG",
        optimize=True,
    )

    rendered: list[Image.Image] = []
    ring_paths = sorted((output_root / "directional").glob("ring_*.png"))
    sequence = ["center", *(path.stem for path in ring_paths), "center"]
    for direction in sequence:
        source = Image.open(
            output_root / "directional" / f"{direction}.png"
        ).convert("RGBA")
        source.thumbnail((540, 540), Image.Resampling.LANCZOS)
        frame = _checkerboard((620, 620))
        offset = (
            (frame.width - source.width) // 2,
            (frame.height - source.height) // 2,
        )
        frame.alpha_composite(source, offset)
        draw = ImageDraw.Draw(frame)
        draw.rounded_rectangle(
            (16, 14, 196, 48),
            radius=8,
            fill=(0, 0, 0, 178),
        )
        draw.text((28, 23), f"mouse: {direction}", fill="white")
        rendered.append(frame.convert("RGB"))
    rendered[0].save(
        review_path,
        "GIF",
        save_all=True,
        append_images=rendered[1:],
        duration=[700] + [90] * (len(rendered) - 2) + [700],
        loop=0,
        optimize=False,
    )
    return review_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clear-top", type=int, default=100)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    manifest = build(
        args.video.resolve(),
        args.output.resolve(),
        clear_top=args.clear_top,
    )
    if args.review:
        build_review(args.output.resolve(), args.review.resolve())
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
