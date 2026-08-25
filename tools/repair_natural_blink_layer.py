"""把夸张眯眼闭合层修复为同身份、短弧线的自然眨眼层。

脚本只在显式眼区内清除旧眼线并绘制浅弧闭眼线，眼区外逐像素沿用睁眼图。
同时输出睁眼—闭眼—睁眼 GIF、并排联系表和哈希报告，便于人工视觉验收。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def file_sha256(path: Path) -> str:
    """返回文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_box(value: str) -> tuple[int, int, int, int]:
    """解析逗号分隔的矩形。"""

    values = tuple(int(item.strip()) for item in value.split(","))
    if len(values) != 4:
        raise argparse.ArgumentTypeError("矩形必须为 left,top,right,bottom")
    left, top, right, bottom = values
    if left >= right or top >= bottom:
        raise argparse.ArgumentTypeError("矩形边界无效")
    return values


def parse_curve(value: str) -> tuple[int, int, int, int, int]:
    """解析闭眼线的起点、终点和中央下沉量。"""

    values = tuple(int(item.strip()) for item in value.split(","))
    if len(values) != 5:
        raise argparse.ArgumentTypeError("曲线必须为 x1,y1,x2,y2,drop")
    return values


def erase_old_lash(
    donor: Image.Image,
    box: tuple[int, int, int, int],
) -> Image.Image:
    """只用邻近肤色替换旧深色眼线，保留原脸的肤色纹理和腮红。"""

    result = donor.copy()
    source_pixels = donor.load()
    pixels = result.load()
    left, top, right, bottom = box
    dark_mask = Image.new("L", result.size, 0)
    dark_pixels = dark_mask.load()
    for y in range(top, bottom):
        for x in range(left, right):
            red, green, blue, alpha = source_pixels[x, y]
            if alpha > 0 and red < 160 and green < 115 and blue < 105:
                dark_pixels[x, y] = 255
    dark_mask = dark_mask.filter(ImageFilter.MaxFilter(15))
    dark_pixels = dark_mask.load()
    global_skin = [
        source_pixels[x, y]
        for y in range(max(0, top - 18), min(result.height, bottom + 19))
        for x in range(left, right)
        if (
            dark_pixels[x, y] == 0
            and source_pixels[x, y][3] > 0
            and source_pixels[x, y][0] > 155
            and source_pixels[x, y][1] > 95
        )
    ]
    if not global_skin:
        raise ValueError(f"眼区 {box} 周围没有足够的肤色像素")
    global_skin_color = tuple(
        sum(color[channel] for color in global_skin) // len(global_skin)
        for channel in range(4)
    )
    for y in range(top, bottom):
        for x in range(left, right):
            if dark_pixels[x, y] == 0:
                continue
            top_sample = None
            bottom_sample = None
            for distance in range(1, 36):
                sample_y = y - distance
                if sample_y < 0:
                    break
                color = source_pixels[x, sample_y]
                if dark_pixels[x, sample_y] == 0 and color[0] > 155 and color[1] > 95:
                    top_sample = (color, distance)
                    break
            for distance in range(1, 36):
                sample_y = y + distance
                if sample_y >= result.height:
                    break
                color = source_pixels[x, sample_y]
                if dark_pixels[x, sample_y] == 0 and color[0] > 155 and color[1] > 95:
                    bottom_sample = (color, distance)
                    break
            if top_sample and bottom_sample:
                top_color, top_distance = top_sample
                bottom_color, bottom_distance = bottom_sample
                total = top_distance + bottom_distance
                pixels[x, y] = tuple(
                    round(
                        (
                            top_color[channel] * bottom_distance
                            + bottom_color[channel] * top_distance
                        )
                        / total
                    )
                    for channel in range(4)
                )
            elif top_sample:
                pixels[x, y] = top_sample[0]
            elif bottom_sample:
                pixels[x, y] = bottom_sample[0]
            else:
                # 靠近头发边缘时不拿脸中央的亮肤色硬填，否则会形成矩形亮块。
                pixels[x, y] = source_pixels[x, y]
    feathered_mask = dark_mask.filter(ImageFilter.GaussianBlur(5))
    return Image.composite(result, donor, feathered_mask)


def draw_curve(
    image: Image.Image,
    curve: tuple[int, int, int, int, int],
    *,
    color: tuple[int, int, int, int],
    width: int,
) -> None:
    """以四倍抗锯齿绘制浅下弧闭眼线。"""

    scale = 4
    x1, y1, x2, y2, drop = curve
    overlay = Image.new(
        "RGBA",
        (image.width * scale, image.height * scale),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(overlay)
    points: list[tuple[int, int]] = []
    for index in range(65):
        t = index / 64
        x = x1 + (x2 - x1) * t
        baseline = y1 + (y2 - y1) * t
        y = baseline + 4 * drop * t * (1 - t)
        points.append((round(x * scale), round(y * scale)))
    draw.line(
        points,
        fill=color,
        width=max(1, width * scale),
        joint="curve",
    )
    overlay = overlay.resize(image.size, Image.Resampling.LANCZOS)
    image.alpha_composite(overlay)


def compose_contact_sheet(open_image: Image.Image, closed_image: Image.Image) -> Image.Image:
    """生成等比例并排联系表。"""

    thumb_height = 560
    scale = thumb_height / open_image.height
    thumb_size = (round(open_image.width * scale), thumb_height)
    opened = open_image.resize(thumb_size, Image.Resampling.LANCZOS)
    closed = closed_image.resize(thumb_size, Image.Resampling.LANCZOS)
    sheet = Image.new(
        "RGBA",
        (thumb_size[0] * 2 + 30, thumb_height + 20),
        (28, 28, 30, 255),
    )
    sheet.alpha_composite(opened, (10, 10))
    sheet.alpha_composite(closed, (thumb_size[0] + 20, 10))
    return sheet


def main() -> int:
    """执行自然眨眼层修复。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", required=True, type=Path)
    parser.add_argument("--donor-closed", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--eye-box", action="append", required=True, type=parse_box)
    parser.add_argument(
        "--composite-box",
        action="append",
        type=parse_box,
        help="覆盖完整原眼睛的区域；省略时与 eye-box 相同",
    )
    parser.add_argument("--curve", action="append", required=True, type=parse_curve)
    parser.add_argument("--preview-gif", required=True, type=Path)
    parser.add_argument("--contact-sheet", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--line-width", type=int, default=8)
    args = parser.parse_args()
    if len(args.eye_box) != len(args.curve):
        parser.error("--eye-box 与 --curve 数量必须一致")
    composite_boxes = args.composite_box or args.eye_box
    if len(composite_boxes) != len(args.eye_box):
        parser.error("--composite-box 与 --eye-box 数量必须一致")

    with Image.open(args.open) as opened_file:
        opened = opened_file.convert("RGBA")
    with Image.open(args.donor_closed) as donor_file:
        donor = donor_file.convert("RGBA")
    if opened.size != donor.size:
        raise ValueError("睁眼图与闭眼供体尺寸必须一致")

    repaired = donor
    for box in args.eye_box:
        repaired = erase_old_lash(repaired, box)
    for curve in args.curve:
        draw_curve(
            repaired,
            curve,
            color=(69, 27, 24, 255),
            width=args.line_width,
        )

    # 眼区外严格使用睁眼原像素，避免帽子、嘴型、发丝或身体发生漂移。
    eye_mask = Image.new("L", opened.size, 0)
    eye_draw = ImageDraw.Draw(eye_mask)
    for left, top, right, bottom in composite_boxes:
        eye_draw.ellipse((left, top, right, bottom), fill=255)
    eye_mask = eye_mask.filter(ImageFilter.GaussianBlur(12))
    final = Image.composite(repaired, opened, eye_mask)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    final.save(args.output)
    args.preview_gif.parent.mkdir(parents=True, exist_ok=True)
    gif_frames = [
        opened.convert("P", palette=Image.Palette.ADAPTIVE),
        final.convert("P", palette=Image.Palette.ADAPTIVE),
        opened.convert("P", palette=Image.Palette.ADAPTIVE),
    ]
    gif_frames[0].save(
        args.preview_gif,
        save_all=True,
        append_images=gif_frames[1:],
        duration=[900, 90, 900],
        loop=0,
        disposal=2,
    )
    args.contact_sheet.parent.mkdir(parents=True, exist_ok=True)
    compose_contact_sheet(opened, final).save(args.contact_sheet)

    outside_mask = Image.eval(eye_mask, lambda value: 255 - value)
    outside_difference = 0
    opened_pixels = opened.load()
    final_pixels = final.load()
    outside_pixels = outside_mask.load()
    for y in range(opened.height):
        for x in range(opened.width):
            if outside_pixels[x, y] > 250 and opened_pixels[x, y] != final_pixels[x, y]:
                outside_difference += 1
    report = {
        "schema_version": 1,
        "status": "candidate",
        "strategy": "open-pixel-identity-with-local-natural-lid",
        "open_sha256": file_sha256(args.open),
        "donor_closed_sha256": file_sha256(args.donor_closed),
        "output_sha256": file_sha256(args.output),
        "eye_boxes": [list(value) for value in args.eye_box],
        "composite_boxes": [list(value) for value in composite_boxes],
        "curves": [list(value) for value in args.curve],
        "line_width": args.line_width,
        "outside_eye_pixel_difference": outside_difference,
        "preview_gif": args.preview_gif.name,
        "preview_gif_sha256": file_sha256(args.preview_gif),
        "contact_sheet": args.contact_sheet.name,
        "contact_sheet_sha256": file_sha256(args.contact_sheet),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
