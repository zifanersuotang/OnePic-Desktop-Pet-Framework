"""按声明式布局把透明部件装配成 Cubism 可导入的分层 PSD。

输入只包含作者层和目标包围盒；脚本负责裁切透明边、等比缩放、定位、预览、
PSD 写入、眨眼眼区对齐和逐层哈希清单。它不读取或修改任何运行时宠物目录。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageOps
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer


def file_sha256(path: Path) -> str:
    """返回文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    """读取并校验最小布局契约。"""

    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("schema_version") != 1:
        raise ValueError("仅支持 schema_version=1")
    canvas = value.get("canvas_size")
    if (
        not isinstance(canvas, list)
        or len(canvas) != 2
        or any(not isinstance(item, int) or item <= 0 for item in canvas)
    ):
        raise ValueError("canvas_size 必须是两个正整数")
    blink_regions = value.get("blink_regions", [])
    if not isinstance(blink_regions, list):
        raise ValueError("blink_regions 必须是矩形数组")
    for index, region in enumerate(blink_regions):
        if (
            not isinstance(region, list)
            or len(region) != 4
            or any(not isinstance(item, int) for item in region)
            or region[0] < 0
            or region[1] < 0
            or region[0] >= region[2]
            or region[1] >= region[3]
            or region[2] > canvas[0]
            or region[3] > canvas[1]
        ):
            raise ValueError(f"blink_regions[{index}] 超出画布或格式无效")
    layers = value.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ValueError("layers 必须是非空数组")
    names: set[str] = set()
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict):
            raise ValueError(f"layers[{index}] 必须是对象")
        name = layer.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(f"layers[{index}].name 缺失或重复")
        names.add(name)
        target = layer.get("target_bbox")
        if (
            not isinstance(target, list)
            or len(target) != 4
            or any(not isinstance(item, int) for item in target)
            or target[0] >= target[2]
            or target[1] >= target[3]
        ):
            raise ValueError(f"{name}: target_bbox 无效")
        if "mirror_x" in layer and not isinstance(layer["mirror_x"], bool):
            raise ValueError(f"{name}: mirror_x 必须是布尔值")
    return value


def align_closed_head_to_open(
    open_head: Image.Image,
    closed_head: Image.Image,
    blink_regions: list[list[int]],
) -> Image.Image:
    """仅在眼区引入闭眼内容，保证帽子、头发和脸轮廓逐像素稳定。"""

    mask = Image.new("L", open_head.size, 0)
    draw = ImageDraw.Draw(mask)
    for left, top, right, bottom in blink_regions:
        radius = max(4, min(right - left, bottom - top) // 5)
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=radius,
            fill=255,
        )
    mask = mask.filter(ImageFilter.GaussianBlur(radius=10))
    return Image.composite(closed_head, open_head, mask)


def place_layer(
    source: Image.Image,
    *,
    canvas_size: tuple[int, int],
    target_bbox: tuple[int, int, int, int],
    mirror_x: bool = False,
) -> Image.Image:
    """裁切 alpha 内容并等比放入目标矩形。"""

    rgba = source.convert("RGBA")
    alpha_bbox = rgba.getchannel("A").getbbox()
    if alpha_bbox is None:
        raise ValueError("图层完全透明")
    trimmed = rgba.crop(alpha_bbox)
    if mirror_x:
        trimmed = ImageOps.mirror(trimmed)
    left, top, right, bottom = target_bbox
    target_width = right - left
    target_height = bottom - top
    scale = min(target_width / trimmed.width, target_height / trimmed.height)
    size = (
        max(1, round(trimmed.width * scale)),
        max(1, round(trimmed.height * scale)),
    )
    resized = trimmed.resize(size, Image.Resampling.LANCZOS)
    x = left + (target_width - size[0]) // 2
    y = top + (target_height - size[1]) // 2
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    canvas.alpha_composite(resized, (x, y))
    return canvas


def build(
    manifest_path: Path,
    output_path: Path,
    preview_path: Path,
    runtime_layers_dir: Path | None = None,
) -> dict:
    """生成 PSD、预览和可审计清单。"""

    manifest = load_manifest(manifest_path)
    root = manifest_path.parent
    canvas_size = tuple(manifest["canvas_size"])
    prepared: list[tuple[dict[str, Any], Path, Image.Image]] = []
    for layer in manifest["layers"]:
        source_path = (root / layer["file"]).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"缺少图层：{source_path}")
        with Image.open(source_path) as source:
            canvas = place_layer(
                source,
                canvas_size=canvas_size,
                target_bbox=tuple(layer["target_bbox"]),
                mirror_x=bool(layer.get("mirror_x", False)),
            )
        prepared.append((layer, source_path, canvas))

    preview = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    for layer, _, canvas in prepared:
        if bool(layer.get("preview_visible", layer.get("visible", True))):
            preview.alpha_composite(canvas)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(preview_path)

    psd = PSDImage.new(mode="RGBA", size=canvas_size, color=(0, 0, 0, 0))
    # Cubism 按 PSD 枚举的逆序建立部件列表；清单采用易读的“底到顶”顺序，
    # 因此这里保持原顺序写入，导入后最高层会正确落在列表顶部。
    for layer, _, canvas in prepared:
        pixel_layer = PixelLayer.frompil(canvas, psd, name=layer["name"])
        pixel_layer.visible = bool(layer.get("visible", True))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    psd.save(output_path)

    runtime_manifest: dict[str, Any] | None = None
    if runtime_layers_dir is not None:
        runtime_layers_dir.mkdir(parents=True, exist_ok=True)
        runtime_layers: list[dict[str, Any]] = []
        roles: set[str] = set()
        blink_regions = manifest.get("blink_regions", [])
        prepared_by_role = {
            str(layer.get("runtime_role", "")).strip(): canvas
            for layer, _, canvas in prepared
            if str(layer.get("runtime_role", "")).strip()
        }
        aligned_closed_head = None
        if blink_regions:
            open_head = prepared_by_role.get("head_open")
            closed_head = prepared_by_role.get("head_closed")
            if open_head is None or closed_head is None:
                raise ValueError("blink_regions 需要 head_open 和 head_closed")
            aligned_closed_head = align_closed_head_to_open(
                open_head,
                closed_head,
                blink_regions,
            )
        for layer, _, canvas in prepared:
            role = str(layer.get("runtime_role", "")).strip()
            if not role:
                continue
            if role in roles:
                raise ValueError(f"runtime_role 重复：{role}")
            roles.add(role)
            pivot = layer.get("pivot")
            if (
                not isinstance(pivot, list)
                or len(pivot) != 2
                or any(not isinstance(item, int) for item in pivot)
            ):
                raise ValueError(f"{role}: pivot 必须是两个整数")
            file_name = f"{role}.png"
            runtime_path = runtime_layers_dir / file_name
            runtime_canvas = (
                aligned_closed_head
                if role == "head_closed" and aligned_closed_head is not None
                else canvas
            )
            runtime_canvas.save(runtime_path)
            runtime_layers.append(
                {
                    "role": role,
                    "file": file_name,
                    "pivot": pivot,
                    "sha256": file_sha256(runtime_path),
                }
            )
        required_roles = {
            "body",
            "arm_left",
            "arm_right",
            "head_open",
            "head_closed",
        }
        missing_roles = sorted(required_roles - roles)
        if missing_roles:
            raise ValueError(
                "运行时分层缺少必要角色：" + ", ".join(missing_roles)
            )
        runtime_manifest = {
            "schema_version": 1,
            "canvas_size": list(canvas_size),
            "layers": runtime_layers,
        }
        if blink_regions:
            runtime_manifest["blink_regions"] = blink_regions
            runtime_manifest["closed_head_alignment"] = "localized_feather"
        runtime_manifest_path = runtime_layers_dir / "layered-idle.json"
        runtime_manifest_path.write_text(
            json.dumps(runtime_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    report = {
        "schema_version": 1,
        "manifest": manifest_path.name,
        "canvas_size": list(canvas_size),
        "psd": output_path.name,
        "psd_sha256": file_sha256(output_path),
        "preview": preview_path.name,
        "preview_sha256": file_sha256(preview_path),
        "layers": [
            {
                "name": layer["name"],
                "file": source_path.relative_to(root).as_posix(),
                "source_sha256": file_sha256(source_path),
                "visible": bool(layer.get("visible", True)),
                "target_bbox": layer["target_bbox"],
                "mirror_x": bool(layer.get("mirror_x", False)),
            }
            for layer, source_path, _ in prepared
        ],
    }
    if runtime_manifest is not None:
        report["runtime_layers"] = runtime_manifest
    report_path = output_path.with_suffix(".build.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="按 JSON 布局装配 Cubism 分层 PSD",
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--preview", required=True, type=Path)
    parser.add_argument(
        "--runtime-layers-dir",
        type=Path,
        help="可选：同时导出可直接由 WebGL 宿主加载的全画布分层",
    )
    return parser.parse_args()


def main() -> int:
    """执行命令。"""

    args = parse_args()
    report = build(
        args.manifest.resolve(),
        args.output.resolve(),
        args.preview.resolve(),
        (
            args.runtime_layers_dir.resolve()
            if args.runtime_layers_dir is not None
            else None
        ),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
