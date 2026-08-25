"""把 Live2D 透明截图与常驻配件清单合成为高清标准角色候选。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


def sha256(path: Path) -> str:
    """返回文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    """读取命令行参数。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--review-output", type=Path, required=True)
    parser.add_argument("--candidate-output", type=Path, required=True)
    parser.add_argument("--canvas-width", type=int, default=560)
    parser.add_argument("--canvas-height", type=int, default=500)
    parser.add_argument("--standing-height", type=int, default=450)
    return parser.parse_args()


def main() -> int:
    """叠加配件、裁出高清审阅图，并生成固定画布候选。"""

    args = parse_args()
    base_path = args.base.resolve()
    manifest_path = args.manifest.resolve()
    if not base_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("基础截图或配件清单不存在")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema_version") != 1:
        raise ValueError("配件清单 schema_version 必须为 1")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ValueError("配件清单必须包含非空 layers")

    with Image.open(base_path).convert("RGBA") as source:
        composed = source.copy()
    model_bbox = composed.getchannel("A").getbbox()
    if model_bbox is None:
        raise ValueError("基础截图没有非透明角色")
    left, top, right, bottom = model_bbox
    model_width = right - left
    model_height = bottom - top
    layer_report: list[dict[str, object]] = []
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValueError("配件 layer 必须是 JSON 对象")
        role = str(layer.get("role", "")).strip()
        file_value = str(layer.get("file", "")).strip()
        position = layer.get("position_ratio")
        width_ratio = layer.get("width_ratio")
        rotation = float(layer.get("rotation_degrees", 0.0))
        if (
            not role
            or not file_value
            or not isinstance(position, list)
            or len(position) != 2
            or not all(isinstance(item, (int, float)) for item in position)
            or not isinstance(width_ratio, (int, float))
            or width_ratio <= 0
        ):
            raise ValueError(f"配件条目无效：{role or file_value}")
        layer_path = (manifest_path.parent / file_value).resolve()
        if manifest_path.parent not in layer_path.parents:
            raise ValueError(f"配件路径越界：{file_value}")
        with Image.open(layer_path).convert("RGBA") as image:
            target_width = max(1, round(model_width * float(width_ratio)))
            target_height = max(
                1, round(image.height * target_width / image.width)
            )
            resized = image.resize(
                (target_width, target_height), Image.Resampling.LANCZOS
            )
        if rotation:
            resized = resized.rotate(
                -rotation,
                resample=Image.Resampling.BICUBIC,
                expand=True,
            )
        center_x = left + model_width * float(position[0])
        center_y = top + model_height * float(position[1])
        paste_x = round(center_x - resized.width / 2)
        paste_y = round(center_y - resized.height / 2)
        composed.alpha_composite(resized, (paste_x, paste_y))
        layer_report.append(
            {
                "role": role,
                "file": file_value,
                "target_bbox": [
                    paste_x,
                    paste_y,
                    paste_x + resized.width,
                    paste_y + resized.height,
                ],
            }
        )

    review_bbox = composed.getchannel("A").getbbox()
    if review_bbox is None:
        raise ValueError("合成结果没有非透明角色")
    margin = max(12, round((review_bbox[3] - review_bbox[1]) * 0.025))
    crop_box = (
        max(0, review_bbox[0] - margin),
        max(0, review_bbox[1] - margin),
        min(composed.width, review_bbox[2] + margin),
        min(composed.height, review_bbox[3] + margin),
    )
    review = composed.crop(crop_box)
    args.review_output.parent.mkdir(parents=True, exist_ok=True)
    if args.review_output.exists() or args.candidate_output.exists():
        raise FileExistsError("输出已存在，拒绝覆盖标准候选")
    review.save(args.review_output, "PNG", optimize=True)

    scale = min(
        args.canvas_width / review.width,
        args.standing_height / review.height,
    )
    target_size = (
        max(1, round(review.width * scale)),
        max(1, round(review.height * scale)),
    )
    fitted = review.resize(target_size, Image.Resampling.LANCZOS)
    canvas = Image.new(
        "RGBA", (args.canvas_width, args.canvas_height), (0, 0, 0, 0)
    )
    x = (args.canvas_width - fitted.width) // 2
    y = args.canvas_height - fitted.height - 10
    canvas.alpha_composite(fitted, (x, y))
    args.candidate_output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.candidate_output, "PNG", optimize=True)

    report = {
        "schema_version": 1,
        "base": str(base_path),
        "base_sha256": sha256(base_path),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "model_bbox": list(model_bbox),
        "layers": layer_report,
        "review_output": str(args.review_output.resolve()),
        "review_sha256": sha256(args.review_output),
        "candidate_output": str(args.candidate_output.resolve()),
        "candidate_sha256": sha256(args.candidate_output),
        "canvas_size": [args.canvas_width, args.canvas_height],
        "standing_height": args.standing_height,
    }
    report_path = args.review_output.with_suffix(".json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
