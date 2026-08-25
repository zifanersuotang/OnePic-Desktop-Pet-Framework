"""把图像编辑结果的脸区回贴到已批准透明角色帧。

职责范围：
- 按角色可见包围盒对齐生成图与原始透明帧；
- 仅在指定脸区内进行羽化合成；
- 保留脸区以外的原始像素与透明轮廓；
- 输出用于视觉验收的版本化 PNG，不覆盖输入文件。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--face-box",
        nargs=4,
        type=int,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        required=True,
    )
    parser.add_argument("--feather", type=float, default=7.0)
    return parser.parse_args()


def visible_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    if image.mode == "RGBA":
        bbox = image.getchannel("A").getbbox()
    else:
        luminance = image.convert("L")
        bbox = luminance.point(lambda value: 255 if value > 25 else 0).getbbox()
    if bbox is None:
        raise ValueError("输入图像没有可见主体")
    return bbox


def align_generated(
    generated: Image.Image,
    target_size: tuple[int, int],
    target_bbox: tuple[int, int, int, int],
) -> Image.Image:
    source_bbox = visible_bbox(generated)
    source_width = source_bbox[2] - source_bbox[0]
    source_height = source_bbox[3] - source_bbox[1]
    target_width = target_bbox[2] - target_bbox[0]
    target_height = target_bbox[3] - target_bbox[1]
    scale = min(target_width / source_width, target_height / source_height)
    resized = generated.resize(
        (
            max(1, round(generated.width * scale)),
            max(1, round(generated.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    source_left = round(source_bbox[0] * scale)
    source_top = round(source_bbox[1] * scale)
    source_right = round(source_bbox[2] * scale)
    source_bottom = round(source_bbox[3] * scale)
    source_center_x = (source_left + source_right) / 2
    source_center_y = (source_top + source_bottom) / 2
    target_center_x = (target_bbox[0] + target_bbox[2]) / 2
    target_center_y = (target_bbox[1] + target_bbox[3]) / 2
    offset = (
        round(target_center_x - source_center_x),
        round(target_center_y - source_center_y),
    )
    canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
    canvas.alpha_composite(resized.convert("RGBA"), offset)
    return canvas


def main() -> int:
    args = parse_args()
    original = Image.open(args.original).convert("RGBA")
    generated = Image.open(args.generated).convert("RGB")
    aligned = align_generated(generated, original.size, visible_bbox(original))

    left, top, right, bottom = args.face_box
    if not (0 <= left < right <= original.width and 0 <= top < bottom <= original.height):
        raise ValueError("脸区必须位于原始画布内")

    mask = Image.new("L", original.size, 0)
    draw = ImageDraw.Draw(mask)
    radius = max(4, min((right - left) // 3, (bottom - top) // 3))
    draw.rounded_rectangle((left, top, right, bottom), radius=radius, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(0.0, args.feather)))
    mask = Image.composite(mask, Image.new("L", original.size, 0), original.getchannel("A"))

    result = Image.composite(aligned, original, mask)
    result.putalpha(original.getchannel("A"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.save(args.output)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
