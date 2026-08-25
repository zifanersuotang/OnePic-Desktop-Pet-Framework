"""从授权的透明 Live2D 捕获制作双腿真实交替的八相位正面走路候选。"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class Phase:
    """描述一个左右脚相位。"""

    name: str
    left_angle: float
    right_angle: float
    left_scale: float
    right_scale: float
    body_scale_y: float
    x: int


PHASES = (
    # PIL 的正角会把画面下方推向右侧；左右腿在接地帧必须向外分开，
    # 否则正视角下会被错误地压成一条腿。
    Phase("contact_right", -7.0, 13.0, 0.94, 1.00, 0.995, -2),
    Phase("down_right", -5.0, 11.0, 0.95, 1.00, 0.970, -1),
    Phase("passing_left", -3.0, 4.0, 0.94, 1.00, 1.005, 1),
    Phase("up_left", -15.0, 7.0, 0.91, 0.99, 1.018, 2),
    Phase("contact_left", -13.0, 7.0, 1.00, 0.94, 0.995, 2),
    Phase("down_left", -11.0, 5.0, 1.00, 0.95, 0.970, 1),
    Phase("passing_right", -4.0, 3.0, 1.00, 0.94, 1.005, -1),
    Phase("up_right", -7.0, 15.0, 0.99, 0.91, 1.018, -2),
)


def sha256(path: Path) -> str:
    """返回文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_subject(path: Path) -> Image.Image:
    """读取透明捕获并裁出完整主体。"""

    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("输入捕获完全透明")
    return image.crop(bbox)


def _rotated_leg(
    leg: Image.Image,
    *,
    angle: float,
    length_scale: float,
    stage_side: int,
) -> Image.Image:
    """围绕腿根旋转完整腿部，不生成重复肢体。"""

    leg = leg.resize(
        (leg.width, max(1, round(leg.height * length_scale))),
        Image.Resampling.LANCZOS,
    )
    layer = Image.new("RGBA", (stage_side, stage_side), (0, 0, 0, 0))
    center = stage_side // 2
    layer.alpha_composite(leg, (center - leg.width // 2, center))
    return layer.rotate(
        angle,
        resample=Image.Resampling.BICUBIC,
        center=(center, center),
    )


def render_raw(subject: Image.Image, phase: Phase) -> Image.Image:
    """按当前相位组合两条腿和保持原图不变的上身。"""

    width, height = subject.size
    hip_y = round(height * 0.56)
    margin = round(height * 0.16)
    stage = Image.new(
        "RGBA",
        (width + margin * 2, height + 40),
        (0, 0, 0, 0),
    )
    stage_side = max(height, 720)
    left_box = (round(width * 0.32), hip_y, round(width * 0.50), height)
    right_box = (round(width * 0.50), hip_y, round(width * 0.68), height)
    left = _rotated_leg(
        subject.crop(left_box),
        angle=phase.left_angle,
        length_scale=phase.left_scale,
        stage_side=stage_side,
    )
    right = _rotated_leg(
        subject.crop(right_box),
        angle=phase.right_angle,
        length_scale=phase.right_scale,
        stage_side=stage_side,
    )
    left_pivot = (margin + round(width * 0.41), hip_y)
    right_pivot = (margin + round(width * 0.59), hip_y)
    stage.alpha_composite(
        left,
        (left_pivot[0] - left.width // 2, left_pivot[1] - left.height // 2),
    )
    stage.alpha_composite(
        right,
        (right_pivot[0] - right.width // 2, right_pivot[1] - right.height // 2),
    )
    body = subject.copy()
    ImageDraw.Draw(body).rectangle(
        (round(width * 0.30), hip_y, round(width * 0.70), height),
        fill=(0, 0, 0, 0),
    )
    stage.alpha_composite(body, (margin, 0))

    bbox = stage.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError(f"相位 {phase.name} 完全透明")
    content = stage.crop(bbox)
    content = content.resize(
        (
            content.width,
            max(1, round(content.height * phase.body_scale_y)),
        ),
        Image.Resampling.LANCZOS,
    )
    normalized = Image.new("RGBA", stage.size, (0, 0, 0, 0))
    x = (stage.width - content.width) // 2 + phase.x
    y = stage.height - content.height - 8
    normalized.alpha_composite(content, (x, y))
    return normalized


def normalize_frames(raw_frames: list[Image.Image]) -> list[Image.Image]:
    """以全部相位的联合边界做统一缩放，保留真实身体起伏。"""

    boxes = [frame.getchannel("A").getbbox() for frame in raw_frames]
    if any(box is None for box in boxes):
        raise ValueError("走路候选包含完全透明相位")
    valid = [box for box in boxes if box is not None]
    union = (
        min(box[0] for box in valid),
        min(box[1] for box in valid),
        max(box[2] for box in valid),
        max(box[3] for box in valid),
    )
    width, height = union[2] - union[0], union[3] - union[1]
    scale = min((560 - 28) / width, (500 - 24) / height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    outputs: list[Image.Image] = []
    for raw in raw_frames:
        crop = raw.crop(union).resize(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (560, 500), (0, 0, 0, 0))
        x = (560 - crop.width) // 2
        y = 500 - crop.height - 12
        canvas.alpha_composite(crop, (x, y))
        outputs.append(canvas)
    return outputs


def make_contact_sheet(frames: list[Image.Image], path: Path) -> None:
    """生成八相位联系表。"""

    sheet = Image.new("RGBA", (720, 368), (236, 240, 247, 255))
    draw = ImageDraw.Draw(sheet)
    for index, (phase, frame) in enumerate(zip(PHASES, frames, strict=True)):
        thumb = frame.copy()
        thumb.thumbnail((168, 150), Image.Resampling.LANCZOS)
        column, row = index % 4, index // 4
        x = column * 180 + (180 - thumb.width) // 2
        y = row * 184 + (150 - thumb.height)
        sheet.alpha_composite(thumb, (x, y))
        draw.rectangle((column * 180, row * 184 + 152, column * 180 + 179, row * 184 + 183), fill=(34, 39, 48, 255))
        draw.text((column * 180 + 6, row * 184 + 161), f"{index + 1:02d} {phase.name}", fill=(255, 255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "PNG", optimize=True)


def main() -> int:
    """生成一个完整八相位候选周期，不直接写入正式 walk/frames。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source_path = args.source.resolve()
    output_dir = args.output_dir.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"输出目录非空，拒绝覆盖：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    subject = load_subject(source_path)
    frames = normalize_frames([render_raw(subject, phase) for phase in PHASES])
    entries = []
    for index, (phase, frame) in enumerate(zip(PHASES, frames, strict=True), start=1):
        path = output_dir / f"walk_{index:02d}.png"
        frame.save(path, "PNG", optimize=True)
        bbox = frame.getchannel("A").getbbox()
        entries.append(
            {
                "index": index,
                "phase": phase.name,
                "path": path.name,
                "sha256": sha256(path),
                "alpha_bbox": list(bbox) if bbox else None,
            }
        )
    preview = output_dir / "walk-eight-phase-preview.gif"
    frames[0].save(
        preview,
        save_all=True,
        append_images=frames[1:],
        duration=110,
        loop=0,
        disposal=2,
        optimize=False,
    )
    sheet = output_dir / "walk-eight-phase-contact-sheet.png"
    make_contact_sheet(frames, sheet)
    report = {
        "schema_version": 1,
        "method": "deterministic_authorized_live2d_two_leg_articulation",
        "source": str(source_path),
        "source_sha256": sha256(source_path),
        "frame_count": 8,
        "phases": entries,
        "preview": preview.name,
        "preview_sha256": sha256(preview),
        "contact_sheet": sheet.name,
        "contact_sheet_sha256": sha256(sheet),
        "status": "generated_pending_visual_review",
    }
    (output_dir / "walk-eight-phase-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
