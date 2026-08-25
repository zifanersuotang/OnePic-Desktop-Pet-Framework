"""
本模块为 See-through 与 Stretchy Studio 辅助绑定实验准备安全、可审计的输入。

职责范围：
- 只接受项目内、非 `user_assets` 的公开 PNG 作为无人值守实验输入；
- 把单张透明全身图确定性拆成 Stretchy Studio 可识别的语义代理图层；
- 输出分层 PSD、逐层 PNG、合成预览、相对路径清单和 SHA-256 报告；
- 重新打开 PSD 并逐像素核对合成结果，防止拆层过程丢失或重复像素；
- 验证外部 See-through PSD 是否至少包含四个 Stretchy 已知语义标签。

这里生成的是“管线烟测代理 PSD”，用于验证导入、骨骼向导和导出适配，不代表
See-through 推理结果，也不能替代 Live2D/Spine 人工视觉验收。工具不会读取私人
素材、修改 `workflow.json`、激活宠物、覆盖运行目录或安装任何依赖。

Agent 快速定位：
- 路径与隐私边界位于 `_validate_paths()`；
- 确定性语义拆分位于 `_semantic_masks()`；
- PSD 构建和往返校验位于 `prepare_proxy_psd()`；
- 外部 PSD 验证位于 `validate_layered_psd()`；
- 命令行入口位于 `main()`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KNOWN_STRETCHY_TAGS = {
    "back hair",
    "bottomwear",
    "face",
    "footwear-l",
    "footwear-r",
    "front hair",
    "handwear-l",
    "handwear-r",
    "legwear-l",
    "legwear-r",
    "mouth",
    "neck",
    "topwear",
}
PROTECTED_OUTPUT_ROOTS = (
    Path("assets/pet"),
    Path("dist"),
    Path("user_assets"),
)


class AssistedRiggingError(ValueError):
    """表示路径、隐私、图像或分层 PSD 不满足实验契约。"""


@dataclass(frozen=True)
class LayerRecord:
    """记录一个代理图层的相对路径、包围框、像素量和哈希。"""

    name: str
    path: str
    bbox: tuple[int, int, int, int]
    nontransparent_pixels: int
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _relative(path: Path, project_root: Path) -> str:
    return path.relative_to(project_root).as_posix()


def _validate_paths(
    source: Path,
    output_dir: Path,
    project_root: Path,
) -> tuple[Path, Path, Path]:
    root = project_root.resolve()
    source_resolved = source.resolve()
    output_resolved = output_dir.resolve(strict=False)

    if not source_resolved.is_file():
        raise AssistedRiggingError(f"输入 PNG 不存在：{source}")
    if source_resolved.suffix.lower() != ".png":
        raise AssistedRiggingError("代理拆层目前只接受 PNG 输入。")
    if source_resolved.is_symlink():
        raise AssistedRiggingError("实验输入不能是符号链接。")
    if not _is_relative_to(source_resolved, root):
        raise AssistedRiggingError("实验输入必须位于项目目录内。")
    if _is_relative_to(source_resolved, root / "user_assets"):
        raise AssistedRiggingError(
            "无人值守辅助绑定禁止读取 user_assets；请在用户明确确认后走宠物工作区流程。"
        )
    if not _is_relative_to(output_resolved, root):
        raise AssistedRiggingError("实验输出必须位于项目目录内。")
    for protected in PROTECTED_OUTPUT_ROOTS:
        if _is_relative_to(output_resolved, root / protected):
            raise AssistedRiggingError(
                f"实验输出不能写入受保护目录：{protected.as_posix()}"
            )
    return source_resolved, output_resolved, root


def _alpha_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise AssistedRiggingError("输入 PNG 完全透明。")
    left, top, right, bottom = bbox
    if right - left < 16 or bottom - top < 32:
        raise AssistedRiggingError("输入主体过小，无法建立全身语义代理层。")
    return bbox


def _semantic_masks(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
) -> dict[str, Image.Image]:
    """按主体归一化坐标把每个非透明像素唯一分配给一个语义层。"""

    width, height = image.size
    left, top, right, bottom = bbox
    body_width = max(1, right - left)
    body_height = max(1, bottom - top)
    alpha = image.getchannel("A")
    masks = {
        name: Image.new("L", (width, height), 0)
        for name in (
            "face",
            "handwear-r",
            "topwear",
            "handwear-l",
            "bottomwear",
            "legwear-r",
            "legwear-l",
            "footwear-r",
            "footwear-l",
        )
    }
    source_pixels = alpha.load()
    mask_pixels = {name: mask.load() for name, mask in masks.items()}

    for y in range(top, bottom):
        ny = (y - top) / body_height
        for x in range(left, right):
            value = source_pixels[x, y]
            if value == 0:
                continue
            nx = (x - left) / body_width
            if ny < 0.34:
                name = "face"
            elif ny < 0.62:
                if nx < 0.31:
                    name = "handwear-r"
                elif nx > 0.69:
                    name = "handwear-l"
                else:
                    name = "topwear"
            elif ny < 0.72:
                name = "bottomwear"
            elif ny < 0.88:
                name = "legwear-r" if nx < 0.5 else "legwear-l"
            else:
                name = "footwear-r" if nx < 0.5 else "footwear-l"
            mask_pixels[name][x, y] = value
    return masks


def _masked_layer(
    source: Image.Image,
    mask: Image.Image,
) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    bbox = mask.getbbox()
    if bbox is None:
        return None
    layer = source.copy()
    layer.putalpha(mask)
    return layer.crop(bbox), bbox


def _nontransparent_pixels(image: Image.Image) -> int:
    histogram = image.getchannel("A").histogram()
    return sum(histogram[1:])


def _roundtrip_deltas(left: Image.Image, right: Image.Image) -> tuple[int, int]:
    if left.size != right.size:
        raise AssistedRiggingError("PSD 往返合成尺寸发生变化。")
    left_rgba = left.convert("RGBA")
    right_rgba = right.convert("RGBA")
    alpha_difference = ImageChops.difference(
        left_rgba.getchannel("A"),
        right_rgba.getchannel("A"),
    )
    alpha_delta = alpha_difference.getextrema()[1]
    left_pixels = left_rgba.load()
    right_pixels = right_rgba.load()
    visible_rgb_delta = 0
    for y in range(left_rgba.height):
        for x in range(left_rgba.width):
            left_pixel = left_pixels[x, y]
            right_pixel = right_pixels[x, y]
            if left_pixel[3] == 0 and right_pixel[3] == 0:
                continue
            visible_rgb_delta = max(
                visible_rgb_delta,
                *(abs(left_pixel[channel] - right_pixel[channel]) for channel in range(3)),
            )
    return alpha_delta, visible_rgb_delta


def prepare_proxy_psd(
    source: Path,
    output_dir: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    """生成语义代理 PSD，并返回可直接写入 JSON 的审计报告。"""

    source_path, target, root = _validate_paths(source, output_dir, project_root)
    target.mkdir(parents=True, exist_ok=True)
    layers_dir = target / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(source_path) as opened:
        source_image = opened.convert("RGBA")
    bbox = _alpha_bbox(source_image)
    masks = _semantic_masks(source_image, bbox)

    psd = PSDImage.new("RGBA", source_image.size, color=(0, 0, 0, 0))
    records: list[LayerRecord] = []
    for index, (name, mask) in enumerate(masks.items(), start=1):
        result = _masked_layer(source_image, mask)
        if result is None:
            continue
        layer_image, layer_bbox = result
        left, top, _, _ = layer_bbox
        filename = f"{index:02d}-{name}.png"
        layer_path = layers_dir / filename
        layer_image.save(layer_path)
        PixelLayer.frompil(
            layer_image,
            parent=psd,
            name=name,
            top=top,
            left=left,
        )
        records.append(
            LayerRecord(
                name=name,
                path=_relative(layer_path, root),
                bbox=layer_bbox,
                nontransparent_pixels=_nontransparent_pixels(layer_image),
                sha256=_sha256(layer_path),
            )
        )

    recognized = [record.name for record in records if record.name in KNOWN_STRETCHY_TAGS]
    if len(recognized) < 4:
        raise AssistedRiggingError(
            f"只生成了 {len(recognized)} 个已知语义层，无法触发 Stretchy 绑定向导。"
        )

    psd_path = target / "onepic-proxy-rig.psd"
    psd.save(psd_path)
    preview_path = target / "composite-preview.png"
    source_image.save(preview_path)

    reopened = PSDImage.open(psd_path)
    composite = reopened.composite(force=True).convert("RGBA")
    alpha_delta, visible_rgb_delta = _roundtrip_deltas(source_image, composite)
    if alpha_delta != 0 or visible_rgb_delta > 1:
        raise AssistedRiggingError(
            "PSD 往返合成与输入不一致："
            f"alpha 最大差值 {alpha_delta}，可见 RGB 最大差值 {visible_rgb_delta}。"
        )

    report: dict[str, object] = {
        "schema_version": 1,
        "artifact_kind": "pipeline-smoke-proxy-psd",
        "quality_claim": (
            "仅用于验证 See-through/Stretchy/Spine 适配链路，不代表 AI 拆层质量。"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": _relative(source_path, root),
            "sha256": _sha256(source_path),
            "canvas": list(source_image.size),
            "subject_bbox": list(bbox),
        },
        "output": {
            "psd": _relative(psd_path, root),
            "psd_sha256": _sha256(psd_path),
            "preview": _relative(preview_path, root),
            "recognized_layer_count": len(recognized),
            "recognized_layers": recognized,
            "roundtrip_alpha_delta": alpha_delta,
            "roundtrip_visible_rgb_delta": visible_rgb_delta,
        },
        "layers": [
            {
                "name": record.name,
                "path": record.path,
                "bbox": list(record.bbox),
                "nontransparent_pixels": record.nontransparent_pixels,
                "sha256": record.sha256,
            }
            for record in records
        ],
        "upstreams": {
            "see_through": {
                "repository": "https://github.com/shitagaki-lab/see-through",
                "role": "正式单图语义拆层；8GB GPU 应使用 NF4 与 group offload。",
            },
            "stretchy_studio": {
                "repository": "https://github.com/MangoLion/stretchystudio",
                "role": "导入分层 PSD、建立骨骼/网格并导出 Spine 4.0。",
            },
        },
        "privacy": {
            "user_assets_read": False,
            "absolute_paths_recorded": False,
            "runtime_modified": False,
        },
    }
    report_path = target / "assisted-rigging-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _canonical_tag(name: str) -> str | None:
    normalized = name.strip().lower().replace("_", "-")
    for tag in KNOWN_STRETCHY_TAGS:
        if normalized == tag:
            return tag
        if normalized.startswith(f"{tag}-") or normalized.startswith(f"{tag} "):
            return tag
    return None


def _iter_pixel_layers(psd: PSDImage) -> Iterable[PixelLayer]:
    for layer in psd.descendants():
        if isinstance(layer, PixelLayer):
            yield layer


def validate_layered_psd(path: Path) -> dict[str, object]:
    """验证一个外部 See-through/Stretchy PSD 的基础可绑定性。"""

    resolved = path.resolve()
    if not resolved.is_file() or resolved.suffix.lower() != ".psd":
        raise AssistedRiggingError(f"PSD 不存在或扩展名错误：{path}")
    psd = PSDImage.open(resolved)
    layer_names = [layer.name for layer in _iter_pixel_layers(psd)]
    recognized = [
        tag
        for name in layer_names
        if (tag := _canonical_tag(name)) is not None
    ]
    if len(recognized) < 4:
        raise AssistedRiggingError(
            f"PSD 只有 {len(recognized)} 个 Stretchy 已知语义层，至少需要 4 个。"
        )
    return {
        "path": resolved.name,
        "canvas": [psd.width, psd.height],
        "pixel_layer_count": len(layer_names),
        "recognized_layer_count": len(recognized),
        "recognized_layers": recognized,
        "sha256": _sha256(resolved),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="准备或验证辅助绑定分层 PSD。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-proxy", help="生成管线烟测代理 PSD")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)

    validate = subparsers.add_parser("validate-psd", help="验证分层 PSD 的语义标签")
    validate.add_argument("--psd", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.command == "prepare-proxy":
            result = prepare_proxy_psd(args.source, args.output_dir)
        else:
            result = validate_layered_psd(args.psd)
    except AssistedRiggingError as exc:
        print(f"[失败] {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
