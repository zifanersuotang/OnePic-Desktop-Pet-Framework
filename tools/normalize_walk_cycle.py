"""把一组透明走路/移动帧按同一比例规范到统一运行画布。

所有帧共享一个缩放因子，避免逐帧独立缩放造成角色忽大忽小；同时允许
为每个相位设置少量水平位移和垂直起伏。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def parse_offsets(value: str, frame_count: int) -> list[int]:
    """解析逗号分隔的整数位移，并核对数量。"""
    offsets = [int(item.strip()) for item in value.split(",") if item.strip()]
    if len(offsets) != frame_count:
        raise ValueError(
            f"位移数量必须等于帧数：收到 {len(offsets)}，需要 {frame_count}"
        )
    return offsets


def load_sprites(input_directory: Path) -> list[tuple[Path, Image.Image]]:
    """读取并裁出每张透明帧的有效人物区域。"""
    paths = sorted(input_directory.glob("walk_*.png"))
    if not paths:
        raise ValueError(f"没有找到 walk_*.png：{input_directory}")

    sprites: list[tuple[Path, Image.Image]] = []
    for path in paths:
        with Image.open(path) as source:
            image = source.convert("RGBA")
        bbox = image.getchannel("A").getbbox()
        if bbox is None:
            raise ValueError(f"帧完全透明：{path}")
        sprites.append((path, image.crop(bbox)))
    return sprites


def normalize_cycle(
    input_directory: Path,
    output_directory: Path,
    canvas_size: tuple[int, int],
    target_height: int,
    padding: int,
    bottom_padding: int,
    horizontal_offsets: list[int],
    vertical_offsets: list[int],
) -> None:
    """使用全组共享比例输出规范帧。"""
    sprites = load_sprites(input_directory)
    maximum_width = max(sprite.width for _, sprite in sprites)
    maximum_height = max(sprite.height for _, sprite in sprites)
    scale = min(
        target_height / maximum_height,
        (canvas_size[0] - padding * 2) / maximum_width,
    )

    output_directory.mkdir(parents=True, exist_ok=True)
    for index, (path, sprite) in enumerate(sprites):
        size = (
            max(1, round(sprite.width * scale)),
            max(1, round(sprite.height * scale)),
        )
        resized = sprite.resize(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        x = (
            (canvas_size[0] - resized.width) // 2
            + horizontal_offsets[index]
        )
        y = (
            canvas_size[1]
            - bottom_padding
            - resized.height
            + vertical_offsets[index]
        )
        canvas.alpha_composite(resized, (x, y))
        canvas.save(output_directory / path.name)

    print(
        f"已规范 {len(sprites)} 帧；画布={canvas_size}；"
        f"共享缩放={scale:.4f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--canvas-width", type=int, default=560)
    parser.add_argument("--canvas-height", type=int, default=500)
    parser.add_argument("--target-height", type=int, default=450)
    parser.add_argument("--padding", type=int, default=16)
    parser.add_argument("--bottom-padding", type=int, default=16)
    parser.add_argument(
        "--horizontal-offsets",
        default="-3,-1,1,3,3,1,-1,-3",
    )
    parser.add_argument(
        "--vertical-offsets",
        default="2,4,2,0,2,4,2,0",
    )
    args = parser.parse_args()

    frame_count = len(list(args.input_directory.glob("walk_*.png")))
    normalize_cycle(
        args.input_directory,
        args.output_directory,
        (args.canvas_width, args.canvas_height),
        args.target_height,
        args.padding,
        args.bottom_padding,
        parse_offsets(args.horizontal_offsets, frame_count),
        parse_offsets(args.vertical_offsets, frame_count),
    )


if __name__ == "__main__":
    main()
