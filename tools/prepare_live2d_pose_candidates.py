"""从授权的透明 Live2D 捕获制作坐下或睡眠候选，不改写客户原图。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def sha256(path: Path) -> str:
    """返回文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def alpha_subject(path: Path) -> Image.Image:
    """读取 RGBA 并裁出完整非透明主体。"""

    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError(f"输入图片完全透明：{path}")
    return image.crop(bbox)


def fit_subject(
    subject: Image.Image,
    *,
    canvas_size: tuple[int, int] = (560, 500),
    max_size: tuple[int, int] = (520, 456),
    bottom_margin: int = 12,
) -> Image.Image:
    """等比缩放主体并按脚底锚点放入透明运行画布。"""

    scale = min(max_size[0] / subject.width, max_size[1] / subject.height)
    size = (
        max(1, round(subject.width * scale)),
        max(1, round(subject.height * scale)),
    )
    resized = subject.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = (canvas_size[0] - resized.width) // 2
    y = canvas_size[1] - resized.height - bottom_margin
    canvas.alpha_composite(resized, (x, y))
    return canvas


def fit_seated_with_cushion(subject: Image.Image) -> Image.Image:
    """在独立粉蓝软垫前放置屈腿角色，用环境锚点明确坐下语义。"""

    canvas_size = (560, 500)
    scale = min(520 / subject.width, 430 / subject.height)
    resized = subject.resize(
        (
            max(1, round(subject.width * scale)),
            max(1, round(subject.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    antialias = 4
    cushion = Image.new(
        "RGBA",
        (canvas_size[0] * antialias, canvas_size[1] * antialias),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(cushion)
    box = tuple(value * antialias for value in (92, 302, 468, 430))
    draw.rounded_rectangle(
        box,
        radius=58 * antialias,
        fill=(247, 211, 226, 245),
        outline=(174, 133, 161, 255),
        width=4 * antialias,
    )
    draw.arc(
        tuple(value * antialias for value in (120, 316, 440, 418)),
        start=8,
        end=172,
        fill=(255, 240, 247, 235),
        width=5 * antialias,
    )
    draw.ellipse(
        tuple(value * antialias for value in (267, 352, 293, 378)),
        fill=(118, 126, 174, 255),
        outline=(81, 87, 133, 255),
        width=2 * antialias,
    )
    cushion = cushion.resize(canvas_size, Image.Resampling.LANCZOS)
    canvas.alpha_composite(cushion)
    x = (canvas_size[0] - resized.width) // 2
    y = canvas_size[1] - resized.height - 16
    canvas.alpha_composite(resized, (x, y))
    return canvas


def seated_subject(
    source: Image.Image,
    *,
    split_ratio: float,
    lower_scale: float,
    spread_ratio: float,
) -> Image.Image:
    """保留上身，压缩并向两侧弯折双腿，得到可审计的屈腿坐姿。"""

    width, height = source.size
    split = round(height * split_ratio)
    overlap = max(8, round(height * 0.018))
    lower_top = max(0, split - overlap)
    upper_bottom = min(height, split + overlap)
    half = width // 2
    lower_height = height - lower_top
    target_lower_height = max(1, round(lower_height * lower_scale))
    spread = max(8, round(width * spread_ratio))

    left = source.crop((0, lower_top, half, height))
    right = source.crop((half, lower_top, width, height))
    left_warp = left.transform(
        (left.width + spread, target_lower_height),
        Image.Transform.AFFINE,
        (
            1.0,
            spread / target_lower_height,
            -spread,
            0.0,
            lower_height / target_lower_height,
            0.0,
        ),
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )
    right_warp = right.transform(
        (right.width + spread, target_lower_height),
        Image.Transform.AFFINE,
        (
            1.0,
            -spread / target_lower_height,
            0.0,
            0.0,
            lower_height / target_lower_height,
            0.0,
        ),
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )

    composed = Image.new(
        "RGBA",
        (width + spread * 2, lower_top + target_lower_height),
        (0, 0, 0, 0),
    )
    composed.alpha_composite(left_warp, (0, lower_top))
    composed.alpha_composite(right_warp, (spread + half, lower_top))
    upper = source.crop((0, 0, width, upper_bottom))
    composed.alpha_composite(upper, (spread, 0))
    bbox = composed.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("坐姿变换产生了完全透明图片")
    return composed.crop(bbox)


def seated_rigid_subject(
    source: Image.Image,
    *,
    leg_angle: float,
    leg_scale: float,
) -> Image.Image:
    """以裙摆下缘为髋点完整旋转双腿，形成清晰的向前外展坐姿。"""

    width, height = source.size
    hip_y = round(height * 0.56)
    left_box = (
        round(width * 0.32),
        hip_y,
        round(width * 0.50),
        height,
    )
    right_box = (
        round(width * 0.50),
        hip_y,
        round(width * 0.68),
        height,
    )
    margin = round(height * 0.35)
    composed = Image.new(
        "RGBA",
        (width + margin * 2, height),
        (0, 0, 0, 0),
    )

    def rotated_leg(box: tuple[int, int, int, int], angle: float) -> Image.Image:
        leg = source.crop(box)
        leg = leg.resize(
            (leg.width, max(1, round(leg.height * leg_scale))),
            Image.Resampling.LANCZOS,
        )
        side = max(leg.height * 2 + leg.width, height)
        layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        center = side // 2
        layer.alpha_composite(leg, (center - leg.width // 2, center))
        return layer.rotate(
            angle,
            resample=Image.Resampling.BICUBIC,
            center=(center, center),
        )

    left = rotated_leg(left_box, leg_angle)
    right = rotated_leg(right_box, -leg_angle)
    left_pivot = (margin + round(width * 0.41), hip_y)
    right_pivot = (margin + round(width * 0.59), hip_y)
    composed.alpha_composite(
        left,
        (left_pivot[0] - left.width // 2, left_pivot[1] - left.height // 2),
    )
    composed.alpha_composite(
        right,
        (right_pivot[0] - right.width // 2, right_pivot[1] - right.height // 2),
    )

    body = source.copy()
    draw = ImageDraw.Draw(body)
    draw.rectangle(
        (
            round(width * 0.30),
            hip_y,
            round(width * 0.70),
            height,
        ),
        fill=(0, 0, 0, 0),
    )
    composed.alpha_composite(body, (margin, 0))
    bbox = composed.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("刚性坐姿变换产生了完全透明图片")
    return composed.crop(bbox)


def sleep_subject(source: Image.Image, angle: float) -> Image.Image:
    """整体侧躺旋转，保留完整四肢、服装与已授权配饰。"""

    return source.rotate(
        angle,
        resample=Image.Resampling.BICUBIC,
        expand=True,
    )


def fit_sleep_with_zzz(subject: Image.Image) -> Image.Image:
    """把完整角色侧躺放入画布，并添加独立的睡眠 Zzz 语义层。"""

    canvas = fit_subject(
        subject,
        canvas_size=(560, 500),
        max_size=(500, 300),
        bottom_margin=42,
    )
    draw = ImageDraw.Draw(canvas)
    font_path = Path("C:/Windows/Fonts/seguisb.ttf")
    font_sizes = (36, 29, 23)
    fonts = []
    for size in font_sizes:
        try:
            fonts.append(ImageFont.truetype(str(font_path), size=size))
        except OSError:
            fonts.append(ImageFont.load_default())
    for (x, y), font in zip(((48, 158), (88, 126), (122, 104)), fonts, strict=True):
        draw.text(
            (x, y),
            "Z",
            font=font,
            fill=(82, 91, 148, 255),
            stroke_width=2,
            stroke_fill=(255, 239, 247, 255),
        )
    return canvas


def contact_sheet(outputs: list[Path], destination: Path) -> None:
    """生成三候选透明联系表。"""

    tile = (420, 390)
    sheet = Image.new("RGBA", (tile[0] * len(outputs), tile[1]), (34, 30, 40, 255))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(outputs):
        with Image.open(path) as opened:
            frame = opened.convert("RGBA")
        frame.thumbnail((380, 340), Image.Resampling.LANCZOS)
        x = index * tile[0] + (tile[0] - frame.width) // 2
        y = 35 + (340 - frame.height)
        sheet.alpha_composite(frame, (x, y))
        draw.text((index * tile[0] + 12, 10), path.stem, fill=(255, 238, 246, 255))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, "PNG", optimize=True)


def main() -> int:
    """生成单个动作的一批三候选并输出哈希报告。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("sit", "sleep"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    source_path = args.source.resolve()
    output_dir = args.output_dir.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"输出目录非空，拒绝覆盖：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    subject = alpha_subject(source_path)

    if args.action == "sit":
        variants = [
            seated_rigid_subject(subject, leg_angle=36.0, leg_scale=0.76),
            seated_rigid_subject(subject, leg_angle=44.0, leg_scale=0.72),
            seated_rigid_subject(subject, leg_angle=50.0, leg_scale=0.68),
        ]
        frames = [fit_seated_with_cushion(item) for item in variants]
    else:
        angles = (76.0, 83.0, 90.0)
        variants = [sleep_subject(subject, angle) for angle in angles]
        frames = [fit_sleep_with_zzz(item) for item in variants]

    outputs: list[Path] = []
    entries: list[dict[str, object]] = []
    for index, frame in enumerate(frames, start=1):
        destination = output_dir / f"{args.action}-candidate-{index:02d}.png"
        frame.save(destination, "PNG", optimize=True)
        outputs.append(destination)
        bbox = frame.getchannel("A").getbbox()
        entries.append(
            {
                "path": destination.name,
                "sha256": sha256(destination),
                "pixel_size": list(frame.size),
                "alpha_bbox": list(bbox) if bbox else None,
            }
        )

    sheet = output_dir / f"{args.action}-candidates-contact-sheet.png"
    contact_sheet(outputs, sheet)
    report = {
        "schema_version": 1,
        "action": args.action,
        "method": "deterministic_authorized_live2d_rgba_pose_transform",
        "source": str(source_path),
        "source_sha256": sha256(source_path),
        "candidate_count": len(outputs),
        "candidates": entries,
        "contact_sheet": sheet.name,
        "contact_sheet_sha256": sha256(sheet),
        "approval_written": False,
    }
    (output_dir / f"{args.action}-candidates-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
