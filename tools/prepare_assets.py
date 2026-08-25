"""
本模块用于将桌面宠物的透明姿态与连续动画图集拆分并规范为应用可直接加载的 PNG 资源。

职责范围：
- 从基础 2 行 3 列图集中保留挥手和开心姿态；
- 按固定六格拆分含独立漫画符号的表情图集，并根据透明列间隔拆分其他连续序列；
- 根据 Alpha 通道裁掉无效空白，并使用同一人物缩放系数放入透明画布；跑步帧保留落地与腾空高度差；
- 生成托盘图标和记录素材来源、尺寸及文件映射的清单；
- 不修改输入图集，也不删除已有原始图片。

Agent 快速定位：
- 基础姿态和网格位置由 BASE_SPRITES 定义；
- 单张素材规范化逻辑位于 normalize_sprite()；
- 文件写入和清单生成位于 prepare_assets()；
- 命令行入口位于 main()。

输入：
- `assets/generated/pet-sprites-alpha.png` 基础透明图集；
- `assets/generated/idle-cycle-v3-alpha.png` 六帧待机呼吸图集；
- `assets/generated/interaction-expressions-v2-alpha.png` 带漫画符号的六种站姿互动表情图集；
- `assets/generated/sit-transition-v4-alpha.png` 已合并腿脚修正的五帧坐下过渡图集；
- `assets/generated/sit-to-sleep-v2-alpha.png` 已合并前两帧修正的五帧坐姿入睡图集；
- `assets/generated/run-cycle-v5-alpha.png` 八帧连续跑步图集；
- `assets/generated/drag-cycle-v1-alpha.png` 三帧拖拽悬空图集；
- `assets/generated/selfie-sequence-v2-alpha.png` 自拍互动图集。

输出：
- `assets/pet/` 下按行为分类的透明 PNG；
- `assets/icons/pet.png` 托盘图标；
- `assets/pet/manifest.json` 素材清单。

外部依赖：
- Pillow；
- Python 标准库 argparse、json、pathlib。

副作用与安全约束：
- 只覆盖脚本明确管理的派生素材文件，不覆盖 `assets/source/` 原始图片；
- 输入图集必须保留，便于后续重新生成或调整参数。

使用示例：
    py tools/prepare_assets.py
    py tools/prepare_assets.py --input assets/generated/pet-sprites-alpha.png
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "assets" / "generated" / "pet-sprites-alpha.png"
DEFAULT_IDLE_INPUT = PROJECT_ROOT / "assets" / "generated" / "idle-cycle-v3-alpha.png"
DEFAULT_EXPRESSION_INPUT = (
    PROJECT_ROOT / "assets" / "generated" / "interaction-expressions-v2-alpha.png"
)
DEFAULT_SIT_INPUT = PROJECT_ROOT / "assets" / "generated" / "sit-transition-v4-alpha.png"
DEFAULT_SLEEP_INPUT = (
    PROJECT_ROOT / "assets" / "generated" / "sit-to-sleep-v2-alpha.png"
)
DEFAULT_WALK_INPUT = PROJECT_ROOT / "assets" / "generated" / "run-cycle-v5-alpha.png"
DEFAULT_DRAG_INPUT = PROJECT_ROOT / "assets" / "generated" / "drag-cycle-v1-alpha.png"
DEFAULT_SELFIE_INPUT = (
    PROJECT_ROOT / "assets" / "generated" / "selfie-sequence-v2-alpha.png"
)
DEFAULT_CANVAS_SIZE = (560, 500)
CANVAS_PADDING = 16
ROW_SPLIT_RATIO = 0.52
TARGET_STANDING_HEIGHT = 450
TARGET_SEATED_HEIGHT = 316


@dataclass(frozen=True)
class SpriteSpec:
    """描述图集中一个姿态的网格位置和目标文件。"""

    name: str
    row: int
    column: int
    output: str


BASE_SPRITES = (
    SpriteSpec("wave", 0, 1, "interact/wave_01.png"),
    SpriteSpec("happy", 1, 0, "interact/happy_01.png"),
)


def alpha_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    """返回非透明内容边界；完全透明时抛出明确错误。"""

    alpha = image.getchannel("A")
    bbox = alpha.point(lambda value: 255 if value > 8 else 0).getbbox()
    if bbox is None:
        raise ValueError("图集单元格不包含可见像素")
    return bbox


def normalize_sprite(
    image: Image.Image,
    scale: float,
    canvas_size: tuple[int, int] = DEFAULT_CANVAS_SIZE,
    baseline_y: int | None = None,
) -> Image.Image:
    """按公共比例缩放内容；可选保留相对序列基线的垂直离地距离。"""

    bbox = alpha_bbox(image)
    cropped = image.crop(bbox)
    target_size = (
        max(1, round(cropped.width * scale)),
        max(1, round(cropped.height * scale)),
    )
    resized = cropped.resize(target_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = (canvas_size[0] - resized.width) // 2
    bottom = canvas_size[1] - CANVAS_PADDING
    if baseline_y is not None:
        bottom -= round((baseline_y - bbox[3]) * scale)
    y = bottom - resized.height
    canvas.alpha_composite(resized, (x, y))
    return canvas


def common_scale(
    images: list[Image.Image],
    anchor_index: int = 0,
    target_height: int = TARGET_STANDING_HEIGHT,
) -> float:
    """计算所有姿态共用的缩放系数，避免每个姿态被独立放大。"""

    boxes = [alpha_bbox(image) for image in images]
    anchor_height = boxes[anchor_index][3] - boxes[anchor_index][1]
    desired = target_height / anchor_height
    max_width = DEFAULT_CANVAS_SIZE[0] - CANVAS_PADDING * 2
    max_height = DEFAULT_CANVAS_SIZE[1] - CANVAS_PADDING * 2
    fit_limits = []
    for left, top, right, bottom in boxes:
        fit_limits.append(
            min(max_width / (right - left), max_height / (bottom - top))
        )
    return min(desired, *fit_limits)


def split_base_sheet(sheet: Image.Image) -> dict[str, Image.Image]:
    """按基础图集实际留白带拆分各姿态。"""

    cell_width = sheet.width // 3
    row_split = round(sheet.height * ROW_SPLIT_RATIO)
    cells: dict[str, Image.Image] = {}
    for spec in BASE_SPRITES:
        left = spec.column * cell_width
        top = 0 if spec.row == 0 else row_split
        right = sheet.width if spec.column == 2 else left + cell_width
        bottom = row_split if spec.row == 0 else sheet.height
        cells[spec.name] = sheet.crop((left, top, right, bottom))
    return cells


def split_horizontal_sheet(sheet: Image.Image, count: int) -> list[Image.Image]:
    """按透明竖向间隔识别帧，并把闪光等小型独立特效并入最近的主体帧。"""

    alpha = sheet.getchannel("A")
    occupied_columns = [
        x
        for x in range(sheet.width)
        if alpha.crop((x, 0, x + 1, sheet.height)).getextrema()[1] > 8
    ]
    spans: list[tuple[int, int]] = []
    if occupied_columns:
        start = previous = occupied_columns[0]
        for x in occupied_columns[1:]:
            if x > previous + 1:
                spans.append((start, previous + 1))
                start = x
            previous = x
        spans.append((start, previous + 1))
    minimum_frame_width = max(2, sheet.width // max(1, count * 20))
    small_spans = [span for span in spans if span[1] - span[0] < minimum_frame_width]
    spans = [span for span in spans if span[1] - span[0] >= minimum_frame_width]
    for small_left, small_right in small_spans:
        if not spans:
            break
        nearest_index = min(
            range(len(spans)),
            key=lambda index: min(
                abs(small_right - spans[index][0]),
                abs(small_left - spans[index][1]),
            ),
        )
        left, right = spans[nearest_index]
        spans[nearest_index] = (min(left, small_left), max(right, small_right))
    spans.sort()
    if len(spans) != count:
        raise ValueError(
            f"横向图集应包含 {count} 个由透明间隔分开的帧，实际识别到 {len(spans)} 个"
        )
    return [sheet.crop((left, 0, right, sheet.height)) for left, right in spans]


def split_equal_horizontal_sheet(sheet: Image.Image, count: int) -> list[Image.Image]:
    """按等宽单元格拆分图集，保留每格内与人物分离的漫画符号。"""

    boundaries = [round(index * sheet.width / count) for index in range(count + 1)]
    return [
        sheet.crop((boundaries[index], 0, boundaries[index + 1], sheet.height))
        for index in range(count)
    ]


def save_sequence(
    images: list[Image.Image],
    output_root: Path,
    directory: str,
    prefix: str,
    preserve_vertical_offset: bool = False,
    target_anchor_height: int = TARGET_STANDING_HEIGHT,
) -> list[str]:
    """以公共缩放保存连续帧，可选保留跑步腾空高度并返回相对路径。"""

    scale = common_scale(images, target_height=target_anchor_height)
    baseline_y = (
        max(alpha_bbox(image)[3] for image in images)
        if preserve_vertical_offset
        else None
    )
    paths = []
    for index, image in enumerate(images, start=1):
        relative = f"{directory}/{prefix}_{index:02d}.png"
        output_path = output_root / relative
        output_path.parent.mkdir(parents=True, exist_ok=True)
        normalize_sprite(image, scale, baseline_y=baseline_y).save(
            output_path,
            "PNG",
            optimize=True,
        )
        paths.append(relative)
    return paths


def prepare_assets(
    input_path: Path,
    idle_input_path: Path,
    expression_input_path: Path,
    sit_input_path: Path,
    sleep_input_path: Path,
    walk_input_path: Path,
    drag_input_path: Path,
    selfie_input_path: Path,
    output_root: Path,
) -> dict[str, object]:
    """拆分基础与连续图集、写入规范素材并返回清单。"""

    with Image.open(input_path) as source:
        sheet = source.convert("RGBA")
    with Image.open(idle_input_path) as source:
        idle_sheet = source.convert("RGBA")
    with Image.open(expression_input_path) as source:
        expression_sheet = source.convert("RGBA")
    with Image.open(sit_input_path) as source:
        sit_sheet = source.convert("RGBA")
    with Image.open(sleep_input_path) as source:
        sleep_sheet = source.convert("RGBA")
    with Image.open(walk_input_path) as source:
        walk_sheet = source.convert("RGBA")
    with Image.open(drag_input_path) as source:
        drag_sheet = source.convert("RGBA")
    with Image.open(selfie_input_path) as source:
        selfie_sheet = source.convert("RGBA")

    base_cells = split_base_sheet(sheet)
    base_scale = common_scale(list(base_cells.values()), anchor_index=0)
    animations: dict[str, list[str]] = {}
    for spec in BASE_SPRITES:
        output_path = output_root / spec.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        normalize_sprite(base_cells[spec.name], base_scale).save(
            output_path,
            "PNG",
            optimize=True,
        )
        animations[spec.name] = [spec.output.replace("\\", "/")]

    animations["idle"] = save_sequence(
        split_horizontal_sheet(idle_sheet, 6),
        output_root,
        "idle",
        "idle",
    )
    expression_paths = save_sequence(
        split_equal_horizontal_sheet(expression_sheet, 6),
        output_root,
        "expressions",
        "expression",
    )
    for name, relative in zip(
        ("happy", "shy", "surprised", "annoyed", "sleepy", "curious"),
        expression_paths,
        strict=True,
    ):
        animations[name] = [relative]
    animations["sit"] = save_sequence(
        split_horizontal_sheet(sit_sheet, 5),
        output_root,
        "sit",
        "sit",
    )
    animations["sleep"] = save_sequence(
        split_horizontal_sheet(sleep_sheet, 5),
        output_root,
        "sleep",
        "sleep",
        target_anchor_height=TARGET_SEATED_HEIGHT,
    )

    animations["walk"] = save_sequence(
        split_horizontal_sheet(walk_sheet, 8),
        output_root,
        "walk",
        "walk",
        preserve_vertical_offset=True,
    )
    animations["drag"] = save_sequence(
        split_horizontal_sheet(drag_sheet, 3),
        output_root,
        "interact",
        "drag",
        preserve_vertical_offset=True,
    )
    animations["selfie"] = save_sequence(
        split_horizontal_sheet(selfie_sheet, 4),
        output_root,
        "interact",
        "selfie",
    )

    icon_path = PROJECT_ROOT / "assets" / "icons" / "pet.png"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(output_root / animations["idle"][0]) as idle:
        icon = idle.convert("RGBA")
        icon.thumbnail((128, 128), Image.Resampling.LANCZOS)
        icon_canvas = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        icon_canvas.alpha_composite(
            icon,
            ((128 - icon.width) // 2, (128 - icon.height) // 2),
        )
        icon_canvas.save(icon_path, "PNG", optimize=True)

    manifest: dict[str, object] = {
        "sources": [
            input_path.relative_to(PROJECT_ROOT).as_posix(),
            idle_input_path.relative_to(PROJECT_ROOT).as_posix(),
            expression_input_path.relative_to(PROJECT_ROOT).as_posix(),
            sit_input_path.relative_to(PROJECT_ROOT).as_posix(),
            sleep_input_path.relative_to(PROJECT_ROOT).as_posix(),
            walk_input_path.relative_to(PROJECT_ROOT).as_posix(),
            drag_input_path.relative_to(PROJECT_ROOT).as_posix(),
            selfie_input_path.relative_to(PROJECT_ROOT).as_posix(),
        ],
        "canvas_size": list(DEFAULT_CANVAS_SIZE),
        "target_standing_height": TARGET_STANDING_HEIGHT,
        "animations": animations,
        "icon": icon_path.relative_to(PROJECT_ROOT).as_posix(),
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="拆分并规范桌面宠物透明素材")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--idle-input", type=Path, default=DEFAULT_IDLE_INPUT)
    parser.add_argument(
        "--expression-input",
        type=Path,
        default=DEFAULT_EXPRESSION_INPUT,
    )
    parser.add_argument("--sit-input", type=Path, default=DEFAULT_SIT_INPUT)
    parser.add_argument("--sleep-input", type=Path, default=DEFAULT_SLEEP_INPUT)
    parser.add_argument("--walk-input", type=Path, default=DEFAULT_WALK_INPUT)
    parser.add_argument("--drag-input", type=Path, default=DEFAULT_DRAG_INPUT)
    parser.add_argument("--selfie-input", type=Path, default=DEFAULT_SELFIE_INPUT)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "assets" / "pet",
    )
    return parser.parse_args()


def main() -> int:
    """执行素材生成并打印清单摘要。"""

    args = parse_args()
    input_path = args.input.resolve()
    idle_input_path = args.idle_input.resolve()
    expression_input_path = args.expression_input.resolve()
    sit_input_path = args.sit_input.resolve()
    sleep_input_path = args.sleep_input.resolve()
    walk_input_path = args.walk_input.resolve()
    drag_input_path = args.drag_input.resolve()
    selfie_input_path = args.selfie_input.resolve()
    output_root = args.output.resolve()
    for path in (
        input_path,
        idle_input_path,
        expression_input_path,
        sit_input_path,
        sleep_input_path,
        walk_input_path,
        drag_input_path,
        selfie_input_path,
    ):
        if not path.is_file():
            raise FileNotFoundError(f"找不到透明图集：{path}")
    manifest = prepare_assets(
        input_path,
        idle_input_path,
        expression_input_path,
        sit_input_path,
        sleep_input_path,
        walk_input_path,
        drag_input_path,
        selfie_input_path,
        output_root,
    )
    frame_count = sum(len(frames) for frames in manifest["animations"].values())
    print(f"已生成 {frame_count} 个姿态或动画帧：{output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
