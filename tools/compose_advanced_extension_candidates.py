"""
本模块从当前宠物已经批准并装配的基础帧确定性合成标准进阶版候选图集。

职责范围：
- 只复用当前宠物 runtime 中已经通过基础门禁的角色像素，不重新生成或换脸；
- 为招牌挥手、专注工作、开心跳跃和等待回应生成带相位的规则图集；
- 从同一中性身体合成六帧微表情，仅在局部脸区替换已批准表情像素；
- 同时输出透明图集与绿色审查底图，供 prepare_extension_sheet.py 拆帧；
- 不修改 pet-config.json、workflow.json、当前 runtime 或任何既有扩展版本。

该工具生成的是待视觉验收候选，不代表扩展动作已经获批或允许激活。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


CANVAS_SIZE = (560, 500)


def file_sha256(path: Path) -> str:
    """返回文件的 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rgba(path: Path) -> Image.Image:
    """读取并验证固定画布 RGBA 帧。"""

    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    if image.size != CANVAS_SIZE:
        raise ValueError(f"{path} 不是 560×500 运行帧")
    if image.getchannel("A").getbbox() is None:
        raise ValueError(f"{path} 是透明空帧")
    return image


def anchored_transform(
    image: Image.Image,
    *,
    angle: float = 0,
    scale_x: float = 1,
    scale_y: float = 1,
    offset_x: int = 0,
    offset_y: int = 0,
) -> Image.Image:
    """围绕主体脚底中心做缩放、旋转和平移，保持透明固定画布。"""

    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("不能变换透明空帧")
    subject = image.crop(bbox)
    resized = subject.resize(
        (
            max(1, round(subject.width * scale_x)),
            max(1, round(subject.height * scale_y)),
        ),
        Image.Resampling.LANCZOS,
    )
    rotated = resized.rotate(
        angle,
        resample=Image.Resampling.BICUBIC,
        expand=True,
    )
    baseline = bbox[3] + offset_y
    center_x = (bbox[0] + bbox[2]) // 2 + offset_x
    x = center_x - rotated.width // 2
    y = baseline - rotated.height
    result = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    result.alpha_composite(rotated, (x, y))
    return result


def normalize_full_character(
    image: Image.Image,
    *,
    target_height: int = 450,
) -> Image.Image:
    """把完整角色按基础运行帧规格居中并对齐到底部基线。"""

    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("完整角色素材为空")
    subject = image.crop(bbox)
    scale = min(
        target_height / subject.height,
        (CANVAS_SIZE[0] - 20) / subject.width,
        (CANVAS_SIZE[1] - 12) / subject.height,
    )
    resized = subject.resize(
        (
            max(1, round(subject.width * scale)),
            max(1, round(subject.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    result = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    result.alpha_composite(
        resized,
        (
            (CANVAS_SIZE[0] - resized.width) // 2,
            CANVAS_SIZE[1] - 12 - resized.height,
        ),
    )
    return result


def feathered_face_patch(
    base: Image.Image,
    source: Image.Image,
    box: tuple[int, int, int, int],
    *,
    blur_radius: float = 5,
) -> Image.Image:
    """把来源脸区羽化合成到同画布基础帧，避免改变身体与镜头。"""

    left, top, right, bottom = box
    mask = Image.new("L", CANVAS_SIZE, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        box,
        radius=max(2, min(right - left, bottom - top) // 5),
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(blur_radius))
    result = base.copy()
    result.alpha_composite(
        Image.composite(source, Image.new("RGBA", CANVAS_SIZE), mask),
    )
    return result


def add_blush(image: Image.Image) -> Image.Image:
    """在脸颊局部增加克制红晕，保持其他像素不变。"""

    overlay = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.ellipse((217, 268, 255, 290), fill=(242, 112, 130, 112))
    draw.ellipse((306, 268, 344, 290), fill=(242, 112, 130, 112))
    for start_x in (225, 235, 317, 327):
        draw.line(
            (start_x, 274, start_x + 6, 282),
            fill=(206, 72, 96, 105),
            width=2,
        )
    overlay = overlay.filter(ImageFilter.GaussianBlur(3))
    result = image.copy()
    result.alpha_composite(overlay)
    return result


def make_sheet(
    frames: list[Image.Image],
    *,
    columns: int,
    output_alpha: Path,
    output_chroma: Path,
) -> None:
    """把有序帧写成规则透明图集和绿色审查底图。"""

    rows = (len(frames) + columns - 1) // columns
    alpha_sheet = Image.new(
        "RGBA",
        (CANVAS_SIZE[0] * columns, CANVAS_SIZE[1] * rows),
        (0, 0, 0, 0),
    )
    for index, frame in enumerate(frames):
        alpha_sheet.alpha_composite(
            frame,
            (
                index % columns * CANVAS_SIZE[0],
                index // columns * CANVAS_SIZE[1],
            ),
        )
    output_alpha.parent.mkdir(parents=True, exist_ok=True)
    alpha_sheet.save(output_alpha, "PNG", optimize=True)
    chroma = Image.new("RGBA", alpha_sheet.size, (0, 255, 0, 255))
    chroma.alpha_composite(alpha_sheet)
    chroma.convert("RGB").save(output_chroma, "PNG", optimize=True)


def compose(workspace: Path, output_dir: Path) -> dict[str, object]:
    """合成五组标准进阶候选并返回审计摘要。"""

    runtime = workspace / "runtime"
    neutral = load_rgba(runtime / "expressions" / "happy_01.png")
    annoyed = load_rgba(runtime / "expressions" / "annoyed_01.png")
    surprised = load_rgba(runtime / "expressions" / "surprised_01.png")
    wave = load_rgba(runtime / "interact" / "wave_01.png")
    selfie = [
        load_rgba(runtime / "interact" / f"selfie_{index:02d}.png")
        for index in range(1, 5)
    ]
    signature_frames = [
        anchored_transform(wave, angle=-1.6, offset_x=-2),
        anchored_transform(wave, angle=1.4, offset_x=2, offset_y=-2),
        anchored_transform(wave, angle=-1.0, offset_x=-1),
        anchored_transform(wave),
    ]
    focus_frames = [
        anchored_transform(selfie[0]),
        anchored_transform(selfie[1], angle=-0.5),
        anchored_transform(selfie[2], offset_y=-1),
        anchored_transform(selfie[3], angle=0.5),
        anchored_transform(selfie[2], offset_y=-1),
        anchored_transform(selfie[1], angle=-0.5),
    ]
    jump_frames = [
        anchored_transform(neutral, scale_x=1.035, scale_y=0.94, offset_y=6),
        anchored_transform(neutral, scale_x=0.985, scale_y=1.02, offset_y=-18),
        anchored_transform(neutral, scale_x=0.98, scale_y=1.025, offset_y=-40),
        anchored_transform(neutral, scale_x=0.99, scale_y=1.01, offset_y=-17),
        anchored_transform(neutral, scale_x=1.025, scale_y=0.965, offset_y=3),
    ]
    wait_frames = [
        anchored_transform(annoyed, angle=-0.8, offset_x=-1),
        anchored_transform(annoyed, angle=0.5, offset_y=-1),
        anchored_transform(annoyed, angle=0.9, offset_x=1),
        anchored_transform(annoyed),
    ]
    skeptical = feathered_face_patch(
        neutral,
        annoyed,
        (214, 218, 283, 270),
    )
    surprised_face = feathered_face_patch(
        neutral,
        surprised,
        (211, 211, 350, 310),
    )
    shy = add_blush(neutral)
    pensive = feathered_face_patch(
        neutral,
        annoyed,
        (214, 218, 347, 309),
    )
    happy_relaxed = feathered_face_patch(
        neutral,
        annoyed,
        (214, 218, 347, 270),
    )
    micro_frames = [
        neutral.copy(),
        skeptical,
        surprised_face,
        shy,
        pensive,
        add_blush(happy_relaxed),
    ]

    packs = {
        "signature-wave-v1": (signature_frames, 2),
        "focus-work-v1": (focus_frames, 3),
        "jump-v1": (jump_frames, 5),
        "wait-response-v1": (wait_frames, 2),
        "micro-expressions-v1": (micro_frames, 3),
    }
    report: dict[str, object] = {
        "schema_version": 1,
        "workspace": workspace.resolve().as_posix(),
        "method": "approved-runtime-pixels-deterministic-transform",
        "identity_anchor": (
            workspace / "candidate" / "approved.png"
        ).resolve().as_posix(),
        "packs": {},
    }
    for name, (frames, columns) in packs.items():
        alpha_path = output_dir / f"{name}-sheet-alpha.png"
        chroma_path = output_dir / f"{name}-sheet-chroma.png"
        make_sheet(
            frames,
            columns=columns,
            output_alpha=alpha_path,
            output_chroma=chroma_path,
        )
        report["packs"][name] = {
            "frame_count": len(frames),
            "columns": columns,
            "rows": (len(frames) + columns - 1) // columns,
            "alpha": alpha_path.resolve().as_posix(),
            "alpha_sha256": file_sha256(alpha_path),
            "chroma": chroma_path.resolve().as_posix(),
            "chroma_sha256": file_sha256(chroma_path),
        }
    return report


def main() -> None:
    """解析参数并生成候选图集。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = compose(args.workspace.resolve(), args.output_dir.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
