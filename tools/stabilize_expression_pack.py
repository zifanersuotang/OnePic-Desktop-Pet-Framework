"""以一张不可变基础脸为锚点，局部装配稳定的多帧微表情包。

职责范围：
- 从 JSON 规格读取允许变化的眼睛、嘴和腮红区域；
- 只在每个区域的内层羽化蒙版内复制对应表情源像素；
- 保证安全区域外、透明轮廓、帽子、额发、脸型和身体逐像素不变；
- 输出版本化 PNG 与逐帧哈希、变化范围和稳定性检查报告。

本工具不生成新形象，不覆盖输入帧，也不代替真实窗口视觉验收。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter


def file_sha256(path: Path) -> str:
    """返回文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def draw_shape(
    draw: ImageDraw.ImageDraw,
    shape: str,
    box: tuple[int, int, int, int],
    *,
    fill: int,
) -> None:
    """按规格绘制椭圆或圆角矩形。"""

    if shape == "ellipse":
        draw.ellipse(box, fill=fill)
        return
    if shape == "rounded_rectangle":
        radius = max(4, min(box[2] - box[0], box[3] - box[1]) // 3)
        draw.rounded_rectangle(box, radius=radius, fill=fill)
        return
    raise ValueError(f"不支持的蒙版形状：{shape}")


def build_region_mask(
    size: tuple[int, int],
    region: dict[str, Any],
) -> tuple[Image.Image, Image.Image]:
    """返回羽化合成蒙版与严格安全边界蒙版。"""

    inner = tuple(int(value) for value in region["inner"])
    safety = tuple(int(value) for value in region["safety"])
    if len(inner) != 4 or len(safety) != 4:
        raise ValueError("inner 与 safety 必须各含四个整数")
    if not (
        0 <= safety[0] <= inner[0] < inner[2] <= safety[2] <= size[0]
        and 0 <= safety[1] <= inner[1] < inner[3] <= safety[3] <= size[1]
    ):
        raise ValueError("表达区域必须位于对应 safety 边界内")
    shape = str(region.get("shape", "rounded_rectangle"))
    feather = float(region.get("feather", 3.0))
    if not 0.0 <= feather <= 12.0:
        raise ValueError("feather 必须为 0–12")

    inner_mask = Image.new("L", size, 0)
    draw_shape(ImageDraw.Draw(inner_mask), shape, inner, fill=255)
    if feather:
        inner_mask = inner_mask.filter(ImageFilter.GaussianBlur(feather))
    safety_mask = Image.new("L", size, 0)
    draw_shape(ImageDraw.Draw(safety_mask), shape, safety, fill=255)
    inner_mask = ImageChops.multiply(inner_mask, safety_mask)
    return inner_mask, safety_mask


def restrict_upper_band_to_source_skin(
    mask: Image.Image,
    source: Image.Image,
    cutoff_y: int,
) -> Image.Image:
    """在指定高度以上只允许肤色源像素进入，避免复制刘海和额头接缝。"""

    if not 0 <= cutoff_y <= source.height:
        raise ValueError("upper_skin_only_y 超出画布")
    restriction = Image.new("L", source.size, 255)
    pixels = source.load()
    restriction_pixels = restriction.load()
    for y in range(cutoff_y):
        for x in range(source.width):
            red, green, blue, alpha = pixels[x, y]
            skin_like = (
                alpha > 8
                and red > 135
                and green > 75
                and blue > 60
                and red >= green
                and red - blue >= 18
            )
            if not skin_like:
                restriction_pixels[x, y] = 0
    return ImageChops.multiply(mask, restriction)


def is_skin_pixel(pixel: tuple[int, int, int, int]) -> bool:
    """判断像素是否可作为局部肤色填充样本。"""

    red, green, blue, alpha = pixel
    return (
        alpha > 8
        and red > 135
        and green > 75
        and blue > 60
        and red >= green
        and red - blue >= 18
    )


def erase_base_features_with_source_skin(
    result: Image.Image,
    base: Image.Image,
    source: Image.Image,
    safety_mask: Image.Image,
    immutable_guard: Image.Image,
    region: dict[str, Any],
) -> Image.Image:
    """先以局部肤色擦除基础眼睛，再叠加闭眼等新特征。"""

    y_min, y_max = (
        int(value) for value in region["erase_y_range"]
    )
    if not 0 <= y_min < y_max <= base.height:
        raise ValueError("erase_y_range 超出画布")
    safety_box = tuple(int(value) for value in region["safety"])
    samples: list[tuple[int, int, int]] = []
    source_pixels = source.load()
    for y in range(safety_box[1], safety_box[3]):
        for x in range(safety_box[0], safety_box[2]):
            pixel = source_pixels[x, y]
            if is_skin_pixel(pixel):
                samples.append(pixel[:3])
    if not samples:
        raise ValueError("闭眼擦除区域没有可用肤色样本")
    skin_color = tuple(
        round(median(sample[channel] for sample in samples))
        for channel in range(3)
    ) + (255,)

    feature_mask = Image.new("L", base.size, 0)
    feature_pixels = feature_mask.load()
    base_pixels = base.load()
    safety_pixels = safety_mask.load()
    for y in range(y_min, y_max):
        for x in range(safety_box[0], safety_box[2]):
            if safety_pixels[x, y] and not is_skin_pixel(base_pixels[x, y]):
                feature_pixels[x, y] = 255
    feature_mask = feature_mask.filter(ImageFilter.MaxFilter(5))
    feature_mask = feature_mask.filter(ImageFilter.GaussianBlur(1.5))
    feature_mask = ImageChops.multiply(feature_mask, safety_mask)
    feature_mask = ImageChops.multiply(
        feature_mask,
        ImageChops.invert(immutable_guard),
    )
    fill = Image.new("RGBA", base.size, skin_color)
    return Image.composite(fill, result, feature_mask)


def build_connected_dark_guard(
    base: Image.Image,
    config: dict[str, Any],
) -> Image.Image:
    """保护与头顶或侧发连通的深色像素，避免眼区蒙版改动刘海。"""

    left, top, right, bottom = (
        int(value) for value in config["roi"]
    )
    threshold = int(config.get("threshold", 140))
    seed_top = int(config.get("seed_top", top + 45))
    seed_left = int(config.get("seed_left", left + 35))
    seed_right = int(config.get("seed_right", right - 35))
    dilation = int(config.get("dilation", 2))
    maximum_y = int(config.get("maximum_y", bottom))
    if not (
        0 <= left < right <= base.width
        and 0 <= top < bottom <= base.height
        and 0 <= threshold <= 255
        and 0 <= dilation <= 8
        and top < maximum_y <= bottom
    ):
        raise ValueError("immutable_connected_dark 配置不合法")

    pixels = base.load()
    width = right - left
    height = bottom - top
    eligible = bytearray(width * height)
    visited = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()
    for y in range(top, bottom):
        for x in range(left, right):
            red, green, blue, alpha = pixels[x, y]
            offset = (y - top) * width + (x - left)
            if alpha > 8 and max(red, green, blue) <= threshold:
                eligible[offset] = 1
                if y <= seed_top or x <= seed_left or x >= seed_right:
                    visited[offset] = 1
                    queue.append((x, y))

    guard = Image.new("L", base.size, 0)
    guard_pixels = guard.load()
    while queue:
        x, y = queue.popleft()
        guard_pixels[x, y] = 255
        for next_x, next_y in (
            (x - 1, y - 1),
            (x, y - 1),
            (x + 1, y - 1),
            (x - 1, y),
            (x + 1, y),
            (x - 1, y + 1),
            (x, y + 1),
            (x + 1, y + 1),
        ):
            if (
                next_x < left
                or next_x >= right
                or next_y < top
                or next_y >= bottom
            ):
                continue
            offset = (next_y - top) * width + (next_x - left)
            if not eligible[offset] or visited[offset]:
                continue
            visited[offset] = 1
            queue.append((next_x, next_y))
    if dilation:
        guard = guard.filter(ImageFilter.MaxFilter(dilation * 2 + 1))
    vertical_cap = Image.new("L", base.size, 0)
    ImageDraw.Draw(vertical_cap).rectangle(
        (left, top, right - 1, maximum_y - 1),
        fill=255,
    )
    guard = ImageChops.multiply(guard, vertical_cap)
    return guard


def main() -> int:
    args = parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
    frame_order = [str(item) for item in spec["frame_order"]]
    base_name = str(spec["base_frame"])
    regions = spec["regions"]
    frame_regions = spec["frame_regions"]
    source_overrides = spec.get("source_overrides", {})
    if not isinstance(source_overrides, dict):
        raise ValueError("source_overrides 必须是 JSON 对象")
    if base_name not in frame_order:
        raise ValueError("base_frame 必须包含在 frame_order 中")

    base_path = args.source_dir / f"{base_name}.png"
    base = Image.open(base_path).convert("RGBA")
    immutable_guard = Image.new("L", base.size, 0)
    if spec.get("immutable_connected_dark"):
        immutable_guard = build_connected_dark_guard(
            base,
            spec["immutable_connected_dark"],
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    records: dict[str, Any] = {}

    for frame_name in frame_order:
        source_relative = source_overrides.get(
            frame_name,
            f"{frame_name}.png",
        )
        source_path = (args.source_dir / str(source_relative)).resolve()
        extension_root = args.source_dir.resolve().parent
        if not source_path.is_relative_to(extension_root):
            raise ValueError(
                f"{frame_name} 的 source override 离开扩展素材目录"
            )
        source = Image.open(source_path).convert("RGBA")
        if source.size != base.size:
            raise ValueError(f"{frame_name} 与基础帧尺寸不一致")
        source_alpha_matches_base = (
            source.getchannel("A").tobytes()
            == base.getchannel("A").tobytes()
        )
        if source.getchannel("A").getbbox() != base.getchannel("A").getbbox():
            raise ValueError(f"{frame_name} 与基础帧可见外框不一致")

        result = base.copy()
        combined_safety = Image.new("L", base.size, 0)
        used_regions = [str(item) for item in frame_regions.get(frame_name, [])]
        for region_name in used_regions:
            if region_name not in regions:
                raise ValueError(
                    f"{frame_name} 引用了未知区域：{region_name}"
                )
            blend_mask, safety_mask = build_region_mask(
                base.size,
                regions[region_name],
            )
            if "upper_skin_only_y" in regions[region_name]:
                blend_mask = restrict_upper_band_to_source_skin(
                    blend_mask,
                    source,
                    int(regions[region_name]["upper_skin_only_y"]),
                )
            if regions[region_name].get(
                "erase_base_features_with_source_skin",
                False,
            ):
                result = erase_base_features_with_source_skin(
                    result,
                    base,
                    source,
                    safety_mask,
                    immutable_guard,
                    regions[region_name],
                )
            blend_mask = ImageChops.multiply(
                blend_mask,
                ImageChops.invert(immutable_guard),
            )
            result = Image.composite(source, result, blend_mask)
            combined_safety = ImageChops.lighter(
                combined_safety,
                safety_mask,
            )
        result.putalpha(base.getchannel("A"))

        difference = ImageChops.difference(base, result).convert("RGB")
        difference_mask = difference.convert("L").point(
            lambda value: 255 if value > 8 else 0
        )
        outside_safety = ImageChops.multiply(
            difference_mask,
            ImageChops.invert(combined_safety),
        )
        outside_histogram = outside_safety.histogram()
        outside_changed_pixels = (
            outside_safety.width * outside_safety.height
            - outside_histogram[0]
        )
        if outside_changed_pixels:
            raise ValueError(
                f"{frame_name} 在允许区域外改变了 "
                f"{outside_changed_pixels} 个像素"
            )

        output_path = args.output_dir / f"{frame_name}.png"
        result.save(output_path, "PNG", optimize=True)
        records[frame_name] = {
            "source": source_path.as_posix(),
            "source_sha256": file_sha256(source_path),
            "output": output_path.as_posix(),
            "output_sha256": file_sha256(output_path),
            "regions": used_regions,
            "source_alpha_matches_base": source_alpha_matches_base,
            "difference_bbox": list(difference.getbbox())
            if difference.getbbox()
            else None,
            "outside_allowed_changed_pixels": outside_changed_pixels,
            "alpha_preserved": (
                result.getchannel("A").tobytes()
                == base.getchannel("A").tobytes()
            ),
        }

    report = {
        "schema_version": 1,
        "status": "passed",
        "base_frame": base_name,
        "base_sha256": file_sha256(base_path),
        "frame_order": frame_order,
        "rule": (
            "仅眼睛、嘴和双颊小蒙版可变；额发、脸型、皮肤主体、"
            "帽子和身体保持基础帧像素"
        ),
        "immutable_guard_pixels": (
            immutable_guard.width * immutable_guard.height
            - immutable_guard.histogram()[0]
        ),
        "frames": records,
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
