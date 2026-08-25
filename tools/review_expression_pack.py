"""为版本化六帧微表情包生成可复核的预览、联系表和哈希报告。

职责范围：
- 按指定顺序读取同尺寸 RGBA 表情帧；
- 生成常规循环 GIF、静态联系表、脸部放大联系表和快速交替 GIF；
- 验证画布、透明通道与可见包围盒一致；
- 记录逐帧 SHA-256、整包哈希和产物哈希。

本工具不修改源帧，也不代替真实 PetWindow 视觉验收。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--pack-name", required=True)
    parser.add_argument("--frames", nargs="+", required=True)
    parser.add_argument(
        "--face-box",
        nargs=4,
        type=int,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        default=(170, 135, 390, 285),
    )
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checkerboard(size: tuple[int, int], cell: int = 12) -> Image.Image:
    image = Image.new("RGB", size, (235, 235, 235))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle(
                    (x, y, min(x + cell, size[0]), min(y + cell, size[1])),
                    fill=(205, 205, 205),
                )
    return image


def flatten(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    resized = image.resize(size, Image.Resampling.LANCZOS)
    board = checkerboard(size)
    board.paste(resized.convert("RGB"), mask=resized.getchannel("A"))
    return board


def labeled_grid(
    frames: list[tuple[str, Image.Image]],
    *,
    cell_size: tuple[int, int],
    crop_box: tuple[int, int, int, int] | None = None,
) -> Image.Image:
    columns = 3
    rows = (len(frames) + columns - 1) // columns
    label_height = 24
    sheet = Image.new(
        "RGB",
        (columns * cell_size[0], rows * (cell_size[1] + label_height)),
        (20, 20, 20),
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (name, frame) in enumerate(frames):
        column = index % columns
        row = index // columns
        x = column * cell_size[0]
        y = row * (cell_size[1] + label_height)
        draw.text((x + 5, y + 6), name, fill=(255, 255, 255), font=font)
        source = frame.crop(crop_box) if crop_box else frame
        sheet.paste(flatten(source, cell_size), (x, y + label_height))
    return sheet


def main() -> int:
    args = parse_args()
    args.review_dir.mkdir(parents=True, exist_ok=True)
    frames: list[tuple[str, Image.Image]] = []
    records: dict[str, dict[str, object]] = {}
    expected_size: tuple[int, int] | None = None
    expected_alpha: bytes | None = None
    expected_bbox: tuple[int, int, int, int] | None = None

    for name in args.frames:
        path = args.frames_dir / f"{name}.png"
        image = Image.open(path).convert("RGBA")
        alpha = image.getchannel("A")
        bbox = alpha.getbbox()
        if expected_size is None:
            expected_size = image.size
            expected_alpha = alpha.tobytes()
            expected_bbox = bbox
        if image.size != expected_size:
            raise ValueError(f"{name} 画布尺寸不一致")
        if alpha.tobytes() != expected_alpha:
            raise ValueError(f"{name} 透明通道不一致")
        if bbox != expected_bbox:
            raise ValueError(f"{name} 可见包围盒不一致")
        frames.append((name, image))
        records[name] = {
            "path": path.as_posix(),
            "sha256": file_sha256(path),
            "size": list(image.size),
            "alpha_bbox": list(bbox) if bbox else None,
        }

    preview_path = args.review_dir / f"{args.pack_name}-preview.gif"
    contact_path = args.review_dir / f"{args.pack_name}-contact-sheet.png"
    face_path = (
        args.review_dir / f"{args.pack_name}-face-stability-contact-sheet.png"
    )
    flicker_path = args.review_dir / f"{args.pack_name}-flicker-test.gif"
    report_path = args.review_dir / f"{args.pack_name}-report.json"

    gif_frames = [image for _, image in frames]
    gif_frames[0].save(
        preview_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=[700] * len(gif_frames),
        loop=0,
        disposal=2,
    )
    labeled_grid(
        frames,
        cell_size=(280, 250),
    ).save(contact_path, "PNG", optimize=True)
    face_box = tuple(args.face_box)
    labeled_grid(
        frames,
        cell_size=(400, 273),
        crop_box=face_box,
    ).save(face_path, "PNG", optimize=True)

    calm = gif_frames[0]
    alternating: list[Image.Image] = []
    durations: list[int] = []
    for expression in gif_frames[1:]:
        alternating.extend((calm, expression))
        durations.extend((110, 220))
    alternating[0].save(
        flicker_path,
        save_all=True,
        append_images=alternating[1:],
        duration=durations,
        loop=0,
        disposal=2,
    )

    bundle_digest = hashlib.sha256()
    ordered_paths = sorted(
        (args.frames_dir / f"{name}.png" for name in args.frames),
        key=lambda item: item.as_posix(),
    )
    for path in ordered_paths:
        bundle_digest.update(path.name.encode("utf-8"))
        bundle_digest.update(b"\0")
        bundle_digest.update(file_sha256(path).encode("ascii"))
        bundle_digest.update(b"\n")
    report = {
        "schema_version": 1,
        "status": "generated_pending_window_qa",
        "pack_name": args.pack_name,
        "frame_order": args.frames,
        "frame_count": len(frames),
        "canvas_size": list(expected_size or ()),
        "shared_alpha_channel": True,
        "shared_alpha_bbox": list(expected_bbox) if expected_bbox else None,
        "bundle_sha256": bundle_digest.hexdigest(),
        "frames": records,
        "artifacts": {
            "preview": {
                "path": preview_path.as_posix(),
                "sha256": file_sha256(preview_path),
            },
            "contact_sheet": {
                "path": contact_path.as_posix(),
                "sha256": file_sha256(contact_path),
            },
            "face_stability_contact_sheet": {
                "path": face_path.as_posix(),
                "sha256": file_sha256(face_path),
            },
            "flicker_test": {
                "path": flicker_path.as_posix(),
                "sha256": file_sha256(flicker_path),
            },
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
