"""
本模块将用户私有的八姿态透明图集转换为桌宠运行所需的 38 帧素材。

职责范围：
- 按四列两行拆分站立、挥手、跑动、自拍、开心、坐下、睡眠和惊讶姿态；
- 将姿态统一到 560×500 透明画布，并保持站姿高度与脚底锚点一致；
- 通过轻微位移和缩放建立可运行的待机、走动、坐下、睡眠、拖拽和自拍序列；
- 将结果写入 Git 忽略的 user_assets/pet，不覆盖公开演示素材。
- 只有首张标准角色形象得到用户确认后才允许批量生成动作；走路生成后仍需单独预览确认。

输入图集必须是 RGBA PNG，姿态顺序固定为从左到右、从上到下八格。
本模块不会修改输入图片，不访问网络，也不会把私人素材复制到公开 assets 目录。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops

from onepic_desktop_pet.workflow import WorkflowError, require_character_approved


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    PROJECT_ROOT
    / "user_assets"
    / "generated"
    / "user_pet_v2"
    / "poses-alpha.png"
)
DEFAULT_WALK_INPUT = (
    PROJECT_ROOT
    / "user_assets"
    / "generated"
    / "user_pet_v2"
    / "walk-physical-v3-fixed-alpha.png"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "user_assets" / "pet"
CANVAS_SIZE = (560, 500)
PADDING = 16
STANDING_HEIGHT = 450
WALK_PHASES = (
    "contact_right",
    "down_right",
    "passing_left",
    "up_left",
    "contact_left",
    "down_left",
    "passing_right",
    "up_right",
)
WALK_MOTION_FACTORS = (0.45, 0.7, 1.2, 1.65, 0.45, 0.7, 1.2, 1.65)


def alpha_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    """返回可见像素边界，拒绝完全透明的单元格。"""

    bbox = image.getchannel("A").point(lambda value: 255 if value > 8 else 0).getbbox()
    if bbox is None:
        raise ValueError("图集单元格不包含可见像素")
    return bbox


def split_transparent_row(row: Image.Image, count: int) -> list[Image.Image]:
    """按透明列间隔拆分一行姿态，避免宽坐姿或睡姿被等宽边界裁切。"""

    alpha = row.getchannel("A")
    occupied = [
        x
        for x in range(row.width)
        if alpha.crop((x, 0, x + 1, row.height)).getextrema()[1] > 8
    ]
    spans: list[tuple[int, int]] = []
    if occupied:
        start = previous = occupied[0]
        for x in occupied[1:]:
            if x > previous + 1:
                spans.append((start, previous + 1))
                start = x
            previous = x
        spans.append((start, previous + 1))

    minimum_width = max(2, row.width // (count * 20))
    small = [span for span in spans if span[1] - span[0] < minimum_width]
    spans = [span for span in spans if span[1] - span[0] >= minimum_width]
    for small_left, small_right in small:
        if not spans:
            break
        nearest = min(
            range(len(spans)),
            key=lambda index: min(
                abs(small_right - spans[index][0]),
                abs(small_left - spans[index][1]),
            ),
        )
        left, right = spans[nearest]
        spans[nearest] = (min(left, small_left), max(right, small_right))
    spans.sort()
    if len(spans) != count:
        raise ValueError(f"一行应包含 {count} 个姿态，实际识别到 {len(spans)} 个")
    return [row.crop((left, 0, right, row.height)) for left, right in spans]


def find_transparent_row_split(sheet: Image.Image) -> int:
    """在图集中央寻找两排行为之间的透明横向留白。"""

    alpha = sheet.getchannel("A")
    start = round(sheet.height * 0.35)
    end = round(sheet.height * 0.65)
    counts = {
        y: sum(
            1
            for x in range(sheet.width)
            if alpha.getpixel((x, y)) > 8
        )
        for y in range(start, end)
    }
    minimum_y = min(counts, key=counts.get)
    low_pixel_threshold = max(8, sheet.width // 100)
    split = minimum_y
    while split + 1 < end and counts[split + 1] <= low_pixel_threshold:
        split += 1
    return split + 1


def split_pose_sheet(sheet: Image.Image) -> dict[str, Image.Image]:
    """按固定四列两行拆分八种用户姿态。"""

    names = (
        "idle",
        "wave",
        "happy",
        "selfie",
        "surprised",
        "annoyed",
        "sit",
        "sleep",
    )
    row_split = find_transparent_row_split(sheet)
    rows = (
        sheet.crop((0, 0, sheet.width, row_split)),
        sheet.crop((0, row_split, sheet.width, sheet.height)),
    )
    cells: dict[str, Image.Image] = {}
    for row_index, row in enumerate(rows):
        for column, cell in enumerate(split_transparent_row(row, 4)):
            name = names[row_index * 4 + column]
            alpha_bbox(cell)
            cells[name] = cell
    return cells


def split_walk_sheet(sheet: Image.Image) -> list[Image.Image]:
    """按从上到下、从左到右顺序拆分八相位走路循环。"""

    row_split = find_transparent_row_split(sheet)
    rows = (
        sheet.crop((0, 0, sheet.width, row_split)),
        sheet.crop((0, row_split, sheet.width, sheet.height)),
    )
    frames = [frame for row in rows for frame in split_transparent_row(row, 4)]
    for frame in frames:
        alpha_bbox(frame)
    return frames


def normalize_pose(
    image: Image.Image,
    target_height: int,
    *,
    bottom_offset: int = 0,
    horizontal_offset: int = 0,
) -> Image.Image:
    """将一个姿态按指定高度置于统一透明画布。"""

    cropped = image.crop(alpha_bbox(image))
    scale = target_height / cropped.height
    max_width = CANVAS_SIZE[0] - PADDING * 2
    if cropped.width * scale > max_width:
        scale = max_width / cropped.width
    size = (
        max(1, round(cropped.width * scale)),
        max(1, round(cropped.height * scale)),
    )
    resized = cropped.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    x = (CANVAS_SIZE[0] - resized.width) // 2 + horizontal_offset
    y = CANVAS_SIZE[1] - PADDING - bottom_offset - resized.height
    canvas.alpha_composite(resized, (x, y))
    return canvas


def save_frames(
    output_root: Path,
    directory: str,
    prefix: str,
    frames: list[Image.Image],
) -> list[str]:
    """保存一组 RGBA 帧并返回相对于私有宠物目录的路径。"""

    paths: list[str] = []
    for index, frame in enumerate(frames, start=1):
        relative = f"{directory}/{prefix}_{index:02d}.png"
        path = output_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.save(path, "PNG", optimize=True)
        paths.append(relative)
    return paths


def repeated_pose(
    pose: Image.Image,
    height: int,
    offsets: list[tuple[int, int]],
) -> list[Image.Image]:
    """用轻微水平和垂直偏移形成简单连续动画。"""

    return [
        normalize_pose(
            pose,
            height,
            horizontal_offset=x_offset,
            bottom_offset=bottom_offset,
        )
        for x_offset, bottom_offset in offsets
    ]


def validate_walk_cycle(frames: list[Image.Image]) -> None:
    """检查走路循环的尺寸、离地、相邻差异和首尾过渡。"""

    if len(frames) != 8:
        raise ValueError(f"走路循环必须包含 8 帧，实际为 {len(frames)} 帧")
    boxes = [alpha_bbox(frame) for frame in frames]
    heights = [bottom - top for _left, top, _right, bottom in boxes]
    bottoms = [bottom for _left, _top, _right, bottom in boxes]
    if max(heights) - min(heights) > 8:
        raise ValueError("走路帧人物高度变化超过 8 像素")
    if max(bottoms) - min(bottoms) < 4:
        raise ValueError("走路帧没有可见的脚底接触与离地变化")

    difference_ratios: list[float] = []
    for current, following in zip(frames, frames[1:] + frames[:1], strict=True):
        difference = ImageChops.difference(current, following).convert("L")
        changed = difference.point(lambda value: 255 if value > 12 else 0)
        changed_pixels = changed.histogram()[255]
        visible_pixels = max(1, sum(current.getchannel("A").histogram()[1:]))
        difference_ratios.append(changed_pixels / visible_pixels)
    if any(ratio < 0.08 for ratio in difference_ratios):
        raise ValueError("走路循环存在过于相似的相邻帧")
    if difference_ratios[-1] > max(difference_ratios[:-1]) * 1.75:
        raise ValueError("走路动画第八帧回到第一帧时跳变过大")


def prepare_user_pet(
    input_path: Path,
    walk_input_path: Path,
    output_root: Path,
) -> dict[str, object]:
    """在角色确认门禁通过后生成私有桌宠帧、图标和清单。"""

    require_character_approved(PROJECT_ROOT / "user_assets" / "workflow.json")

    with Image.open(input_path) as source:
        sheet = source.convert("RGBA")
    with Image.open(walk_input_path) as source:
        walk_sheet = source.convert("RGBA")
    poses = split_pose_sheet(sheet)
    walk_frames = split_walk_sheet(walk_sheet)

    animations: dict[str, list[str]] = {}
    animations["idle"] = save_frames(
        output_root,
        "idle",
        "idle",
        repeated_pose(poses["idle"], STANDING_HEIGHT, [(0, 0), (0, 1), (1, 2), (0, 2), (-1, 1), (0, 0)]),
    )
    normalized_walk_frames = [
        normalize_pose(frame, STANDING_HEIGHT, bottom_offset=offset)
        for frame, offset in zip(
            walk_frames,
            (0, 0, 2, 5, 0, 0, 2, 5),
            strict=True,
        )
    ]
    validate_walk_cycle(normalized_walk_frames)
    animations["walk"] = save_frames(
        output_root,
        "walk",
        "walk",
        normalized_walk_frames,
    )
    normalized_walk_frames[0].save(
        output_root / "walk-preview.gif",
        save_all=True,
        append_images=normalized_walk_frames[1:],
        duration=90,
        loop=0,
        disposal=2,
    )
    animations["sit"] = save_frames(
        output_root,
        "sit",
        "sit",
        [
            normalize_pose(poses["idle"], 450),
            normalize_pose(poses["idle"], 405),
            normalize_pose(poses["idle"], 360),
            normalize_pose(poses["sit"], 335),
            normalize_pose(poses["sit"], 316),
        ],
    )
    animations["sleep"] = save_frames(
        output_root,
        "sleep",
        "sleep",
        [
            normalize_pose(poses["sit"], 316),
            normalize_pose(poses["sit"], 290),
            normalize_pose(poses["sleep"], 260),
            normalize_pose(poses["sleep"], 245),
            normalize_pose(poses["sleep"], 235),
        ],
    )
    animations["drag"] = save_frames(
        output_root,
        "interact",
        "drag",
        repeated_pose(poses["surprised"], STANDING_HEIGHT, [(-3, 12), (0, 18), (3, 12)]),
    )
    animations["selfie"] = save_frames(
        output_root,
        "interact",
        "selfie",
        repeated_pose(poses["selfie"], STANDING_HEIGHT, [(0, 0), (-1, 2), (1, 3), (0, 0)]),
    )

    single_poses = {
        "wave": "wave",
        "happy": "happy",
        "shy": "happy",
        "surprised": "surprised",
        "annoyed": "annoyed",
        "sleepy": "idle",
        "curious": "wave",
    }
    for state, pose_name in single_poses.items():
        directory = "interact" if state == "wave" else "expressions"
        animations[state] = save_frames(
            output_root,
            directory,
            state,
            [normalize_pose(poses[pose_name], STANDING_HEIGHT)],
        )

    icon_path = output_root / "icon.png"
    with Image.open(output_root / animations["idle"][0]) as idle:
        icon = idle.convert("RGBA")
        icon.thumbnail((128, 128), Image.Resampling.LANCZOS)
        icon_canvas = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        icon_canvas.alpha_composite(icon, ((128 - icon.width) // 2, (128 - icon.height) // 2))
        icon_canvas.save(icon_path, "PNG", optimize=True)

    manifest: dict[str, object] = {
        "sources": [
            input_path.relative_to(PROJECT_ROOT).as_posix(),
            walk_input_path.relative_to(PROJECT_ROOT).as_posix(),
        ],
        "canvas_size": list(CANVAS_SIZE),
        "target_standing_height": STANDING_HEIGHT,
        "walk_phases": list(WALK_PHASES),
        "walk_motion_factors": list(WALK_MOTION_FACTORS),
        "animations": animations,
        "icon": "icon.png",
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="从八姿态图集生成私有桌宠素材")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--walk-input", type=Path, default=DEFAULT_WALK_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    """执行私有素材生成并打印摘要。"""

    args = parse_args()
    input_path = args.input.resolve()
    walk_input_path = args.walk_input.resolve()
    output_root = args.output.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"找不到八姿态透明图集：{input_path}")
    if not walk_input_path.is_file():
        raise FileNotFoundError(f"找不到八相位走路图集：{walk_input_path}")
    try:
        manifest = prepare_user_pet(input_path, walk_input_path, output_root)
    except WorkflowError as exc:
        print(f"流程未通过：{exc}")
        return 2
    frame_count = sum(len(paths) for paths in manifest["animations"].values())
    print(f"已生成 {frame_count} 个私有桌宠帧：{output_root}")
    print("下一步必须运行 onepic_workflow.py walk-review，并让用户查看 GIF。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
