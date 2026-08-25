"""
本模块把已去背的规则网格动作图集拆成可复用桌宠扩展帧，并生成视觉验收材料。

职责范围：
- 按声明的行列和帧名拆分 RGBA 图集，不猜测文件顺序；
- 使用同一缩放系数统一同组角色体积，并输出固定 560×500 透明画布；
- 普通动作固定脚底基线；跳跃类动作可保留图集中相对地面的垂直位移；
- 检查透明四角、裁切、主体覆盖率和帧数；
- 输出 GIF、联系表和包含源图/逐帧 SHA-256 的可审计报告。

本工具只处理已经生成并获准进入当前宠物私有工作区的图集，不访问网络，
不修改标准角色、八动作、走路门禁或当前激活 runtime。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def file_sha256(path: Path) -> str:
    """返回文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_canvas(value: str) -> tuple[int, int]:
    """解析 WIDTHxHEIGHT 画布参数。"""

    try:
        width_text, height_text = value.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("画布必须写成 WIDTHxHEIGHT") from error
    if width < 64 or height < 64:
        raise argparse.ArgumentTypeError("画布尺寸不能小于 64×64")
    return width, height


def split_cells(
    sheet: Image.Image,
    columns: int,
    rows: int,
    count: int,
) -> list[Image.Image]:
    """按比例边界拆分图集，安全处理不能整除的末行末列。"""

    cells: list[Image.Image] = []
    for index in range(count):
        column = index % columns
        row = index // columns
        left = round(column * sheet.width / columns)
        right = round((column + 1) * sheet.width / columns)
        top = round(row * sheet.height / rows)
        bottom = round((row + 1) * sheet.height / rows)
        cells.append(sheet.crop((left, top, right, bottom)))
    return cells


def alpha_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    """返回非透明主体边界；空帧直接失败。"""

    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("图集中出现完全透明的空帧")
    return bbox


def remove_small_alpha_components(
    image: Image.Image,
    *,
    minimum_pixels: int = 256,
) -> Image.Image:
    """清除去背后孤立的小色块，同时保留脚、发夹等有效独立部件。"""

    alpha = image.getchannel("A")
    width, height = alpha.size
    alpha_pixels = alpha.load()
    visited = bytearray(width * height)
    remove: list[tuple[int, int]] = []
    for y in range(height):
        for x in range(width):
            offset = y * width + x
            if visited[offset] or alpha_pixels[x, y] < 16:
                continue
            queue: deque[tuple[int, int]] = deque([(x, y)])
            visited[offset] = 1
            component: list[tuple[int, int]] = []
            while queue:
                current_x, current_y = queue.popleft()
                component.append((current_x, current_y))
                for next_x, next_y in (
                    (current_x - 1, current_y),
                    (current_x + 1, current_y),
                    (current_x, current_y - 1),
                    (current_x, current_y + 1),
                ):
                    if (
                        next_x < 0
                        or next_y < 0
                        or next_x >= width
                        or next_y >= height
                    ):
                        continue
                    next_offset = next_y * width + next_x
                    if visited[next_offset] or alpha_pixels[next_x, next_y] < 16:
                        continue
                    visited[next_offset] = 1
                    queue.append((next_x, next_y))
            if len(component) < minimum_pixels:
                remove.extend(component)
    if not remove:
        return image
    cleaned_alpha = alpha.copy()
    cleaned_pixels = cleaned_alpha.load()
    for x, y in remove:
        cleaned_pixels[x, y] = 0
    cleaned = image.copy()
    cleaned.putalpha(cleaned_alpha)
    return cleaned


def prepare_frames(
    cells: list[Image.Image],
    *,
    canvas_size: tuple[int, int],
    target_subject_height: int,
    preserve_vertical_motion: bool,
) -> list[Image.Image]:
    """以共享比例装配固定画布帧，并按需保留跳跃高度。"""

    cells = [remove_small_alpha_components(cell) for cell in cells]
    bboxes = [alpha_bbox(cell) for cell in cells]
    subject_heights = [bbox[3] - bbox[1] for bbox in bboxes]
    subject_widths = [bbox[2] - bbox[0] for bbox in bboxes]
    median_height = statistics.median(subject_heights)
    canvas_width, canvas_height = canvas_size
    source_baseline = max(bbox[3] for bbox in bboxes)
    vertical_extents = [
        height + (source_baseline - bbox[3])
        for height, bbox in zip(subject_heights, bboxes, strict=True)
    ]
    scale = min(
        target_subject_height / median_height,
        (canvas_width - 20) / max(subject_widths),
        (canvas_height - 20)
        / max(vertical_extents if preserve_vertical_motion else subject_heights),
    )
    target_baseline = canvas_height - 12
    frames: list[Image.Image] = []
    for cell, bbox in zip(cells, bboxes, strict=True):
        subject = cell.crop(bbox)
        resized = subject.resize(
            (
                max(1, round(subject.width * scale)),
                max(1, round(subject.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        vertical_gap = (
            round((source_baseline - bbox[3]) * scale)
            if preserve_vertical_motion
            else 0
        )
        destination_bottom = target_baseline - vertical_gap
        x = (canvas_width - resized.width) // 2
        y = destination_bottom - resized.height
        if x < 0 or y < 0 or x + resized.width > canvas_width:
            raise ValueError("扩展帧主体超出画布，请降低 target_subject_height")
        frame = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        frame.alpha_composite(resized, (x, y))
        frames.append(frame)
    return frames


def make_preview(
    frames: list[Image.Image],
    destination: Path,
    duration_ms: int,
) -> None:
    """生成白底循环 GIF，便于用户检查动作连贯性。"""

    preview_frames: list[Image.Image] = []
    for frame in frames:
        background = Image.new("RGBA", frame.size, "white")
        background.alpha_composite(frame)
        preview_frames.append(background.convert("P", palette=Image.Palette.ADAPTIVE))
    preview_frames[0].save(
        destination,
        save_all=True,
        append_images=preview_frames[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
        optimize=False,
    )


def make_contact_sheet(
    names: list[str],
    frames: list[Image.Image],
    destination: Path,
) -> None:
    """生成带帧名的两行棋盘格联系表。"""

    columns = min(5, len(frames))
    rows = (len(frames) + columns - 1) // columns
    cell_width = 260
    cell_height = 250
    sheet = Image.new(
        "RGB",
        (columns * cell_width, rows * cell_height),
        "#eeeeee",
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (name, frame) in enumerate(zip(names, frames, strict=True)):
        column = index % columns
        row = index // columns
        x0 = column * cell_width
        y0 = row * cell_height
        checker = Image.new("RGB", (cell_width, cell_height - 22), "white")
        checker_draw = ImageDraw.Draw(checker)
        block = 18
        for y in range(0, checker.height, block):
            for x in range(0, checker.width, block):
                if (x // block + y // block) % 2:
                    checker_draw.rectangle(
                        (x, y, x + block - 1, y + block - 1),
                        fill="#d7d7d7",
                    )
        preview = frame.copy()
        preview.thumbnail(
            (cell_width - 8, cell_height - 30),
            Image.Resampling.LANCZOS,
        )
        checker_rgba = checker.convert("RGBA")
        checker_rgba.alpha_composite(
            preview,
            (
                (checker.width - preview.width) // 2,
                (checker.height - preview.height) // 2,
            ),
        )
        sheet.paste(checker_rgba.convert("RGB"), (x0, y0 + 22))
        draw.rectangle((x0, y0, x0 + cell_width, y0 + 22), fill="#222222")
        draw.text((x0 + 6, y0 + 5), name, fill="white", font=font)
    sheet.save(destination, "PNG", optimize=True)


def prepare_sheet(args: argparse.Namespace) -> dict[str, object]:
    """拆帧、生成预览并返回可序列化报告。"""

    source = args.input.resolve()
    output_root = args.output_dir.resolve()
    review_root = args.review_dir.resolve()
    names = [item.strip() for item in args.frame_names.split(",") if item.strip()]
    capacity = args.columns * args.rows
    if len(names) == 0 or len(names) > capacity:
        raise ValueError("帧名数量必须大于 0 且不超过网格容量")
    if len(set(names)) != len(names):
        raise ValueError("帧名不能重复")
    with Image.open(source) as opened:
        sheet = opened.convert("RGBA")
    cells = split_cells(sheet, args.columns, args.rows, len(names))
    frames = prepare_frames(
        cells,
        canvas_size=args.canvas,
        target_subject_height=args.target_subject_height,
        preserve_vertical_motion=args.preserve_vertical_motion,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    review_root.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []
    for index, (name, frame) in enumerate(zip(names, frames, strict=True), start=1):
        path = output_root / f"{name}.png"
        frame.save(path, "PNG", optimize=True)
        corners = (
            frame.getpixel((0, 0))[3],
            frame.getpixel((frame.width - 1, 0))[3],
            frame.getpixel((0, frame.height - 1))[3],
            frame.getpixel((frame.width - 1, frame.height - 1))[3],
        )
        if any(corners):
            raise ValueError(f"扩展帧 {name} 的透明四角检查失败")
        bbox = alpha_bbox(frame)
        files.append(
            {
                "index": index,
                "name": name,
                "path": path.as_posix(),
                "sha256": file_sha256(path),
                "alpha_bbox": list(bbox),
            }
        )
    gif = review_root / f"{args.pack_name}-preview.gif"
    sheet_path = review_root / f"{args.pack_name}-contact-sheet.png"
    make_preview(frames, gif, args.duration_ms)
    make_contact_sheet(names, frames, sheet_path)
    report = {
        "schema_version": 1,
        "pack_name": args.pack_name,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": source.as_posix(),
        "source_sha256": file_sha256(source),
        "grid": {"columns": args.columns, "rows": args.rows},
        "canvas_size": list(args.canvas),
        "target_subject_height": args.target_subject_height,
        "preserve_vertical_motion": args.preserve_vertical_motion,
        "duration_ms": args.duration_ms,
        "frame_count": len(frames),
        "frames": files,
        "preview": gif.as_posix(),
        "contact_sheet": sheet_path.as_posix(),
        "status": "candidate_needs_user_review",
    }
    report_path = review_root / f"{args.pack_name}-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    """创建命令行解析器。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--pack-name", required=True)
    parser.add_argument("--columns", type=int, required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--frame-names", required=True)
    parser.add_argument("--canvas", type=parse_canvas, default=(560, 500))
    parser.add_argument("--target-subject-height", type=int, required=True)
    parser.add_argument("--duration-ms", type=int, required=True)
    parser.add_argument("--preserve-vertical-motion", action="store_true")
    return parser


def main() -> None:
    """执行命令并打印报告。"""

    args = build_parser().parse_args()
    if args.columns < 1 or args.rows < 1:
        raise ValueError("网格行列必须大于 0")
    if args.target_subject_height < 64:
        raise ValueError("主体目标高度不能小于 64")
    if args.duration_ms < 20:
        raise ValueError("单帧时长不能小于 20 ms")
    print(json.dumps(prepare_sheet(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
