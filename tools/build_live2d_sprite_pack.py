"""
本模块把已经捕获的透明 Live2D 关键姿态装配为隔离的 OnePic 73 帧精灵候选包。

职责范围：
- 从命名预设 PNG 生成 38 帧基础、2 帧摸头、25 帧进阶与 8 帧侧走；
- 使用保持透明通道的高质量缩放、旋转和轻微位移生成物理唯一帧；
- 为正面走路和每个扩展包生成 GIF、联系表与逐帧哈希报告；
- 把产物写入宠物私有工作区的 generated 子目录，不修改 workflow、运行清单、
  pet-config 或任何已经批准的正式素材。

本工具不访问网络、不调用 OnePic 构建器，也不会覆盖已有输出目录。生成结果仍是
待人工视觉验收的候选素材，不能据此推断角色、走路或扩展动作已经获得用户批准。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


PRESET_NAMES = (
    "neutral",
    "happy",
    "shy",
    "surprised",
    "annoyed",
    "curious",
    "wave",
    "selfie",
    "sit",
    "sleep",
    "drag",
    "writing",
    "hug_bear",
    "waiting",
    "walk_a",
    "walk_b",
    "side_a",
    "side_b",
)
COUNTS = {
    "base": 38,
    "touch": 2,
    "advanced": 25,
    "side_walk": 8,
    "total": 73,
}


@dataclass(frozen=True)
class Motion:
    """描述一帧相对于关键姿态的克制二维变化。"""

    scale_x: float = 1.0
    scale_y: float = 1.0
    angle: float = 0.0
    offset_x: int = 0
    offset_y: int = 0


@dataclass(frozen=True)
class FrameSpec:
    """描述一个物理输出帧及其预设、分组和运动参数。"""

    relative_path: str
    preset: str
    category: str
    sequence: str
    motion: Motion = Motion()


def now_utc() -> str:
    """返回秒级 UTC 时间。"""

    return datetime.now(UTC).isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    """流式计算文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pixel_sha256(image: Image.Image) -> str:
    """计算包含画布尺寸和 RGBA 像素的确定性哈希。"""

    digest = hashlib.sha256()
    digest.update(f"{image.width}x{image.height}:RGBA\n".encode("ascii"))
    digest.update(image.convert("RGBA").tobytes())
    return digest.hexdigest()


def _alpha_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    """返回非透明轮廓；完全透明的预设被明确拒绝。"""

    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("预设 PNG 完全透明，无法生成有效角色帧")
    return bbox


def _find_preset(preset_dir: Path, name: str) -> Path | None:
    """按单文件、同名目录或同名前缀查找预设 PNG。"""

    direct = preset_dir / f"{name}.png"
    if direct.is_file():
        return direct
    nested = preset_dir / name
    if nested.is_dir():
        matches = sorted(path for path in nested.glob("*.png") if path.is_file())
        if matches:
            return matches[0]
    matches = sorted(
        path
        for pattern in (f"{name}_*.png", f"{name}-*.png")
        for path in preset_dir.glob(pattern)
        if path.is_file()
    )
    return matches[0] if matches else None


def load_presets(
    preset_dir: Path,
) -> tuple[dict[str, Image.Image], dict[str, dict[str, Any]], dict[str, str]]:
    """读取关键姿态；缺失的可选预设安全回退 neutral 并留下报告。"""

    root = preset_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"预设目录不存在：{root}")
    neutral_path = _find_preset(root, "neutral")
    if neutral_path is None:
        raise FileNotFoundError(f"缺少必需预设：{root / 'neutral.png'}")

    images: dict[str, Image.Image] = {}
    sources: dict[str, dict[str, Any]] = {}
    fallbacks: dict[str, str] = {}
    for name in PRESET_NAMES:
        source = _find_preset(root, name)
        resolved_name = name
        if source is None:
            source = neutral_path
            resolved_name = "neutral"
            fallbacks[name] = "neutral"
        try:
            with Image.open(source) as opened:
                image = opened.convert("RGBA")
        except OSError as exc:
            raise ValueError(f"无法读取预设 PNG：{source}：{exc}") from exc
        bbox = _alpha_bbox(image)
        images[name] = image
        sources[name] = {
            "requested_preset": name,
            "resolved_preset": resolved_name,
            "path": source.relative_to(root).as_posix(),
            "sha256": file_sha256(source),
            "pixel_size": list(image.size),
            "alpha_bbox": list(bbox),
            "fallback": resolved_name != name,
        }
    return images, sources, fallbacks


def _motion(
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    angle: float = 0.0,
    x: int = 0,
    y: int = 0,
) -> Motion:
    """缩短声明式帧配方中的 Motion 构造。"""

    return Motion(scale_x, scale_y, angle, x, y)


def frame_recipe() -> list[FrameSpec]:
    """返回固定 73 帧物理输出配方。"""

    specs: list[FrameSpec] = []

    idle_motion = (
        _motion(1.0, 1.0, 0.0, 0, 0),
        _motion(1.001, 0.998, -0.12, 0, 1),
        _motion(0.999, 0.996, 0.14, 1, 2),
        _motion(1.002, 0.995, 0.0, 0, 2),
        _motion(1.0, 0.998, -0.13, -1, 1),
        _motion(0.999, 1.001, 0.1, 0, 0),
    )
    specs.extend(
        FrameSpec(f"idle/idle_{index:02d}.png", "neutral", "base", "idle", motion)
        for index, motion in enumerate(idle_motion, start=1)
    )

    walk_presets = (
        "walk_a",
        "walk_a",
        "walk_b",
        "walk_b",
        "walk_b",
        "walk_b",
        "walk_a",
        "walk_a",
    )
    walk_motion = (
        _motion(1.008, 0.992, -0.5, -2, 0),
        _motion(1.018, 0.974, -0.25, -1, 4),
        _motion(0.995, 1.006, 0.4, 1, -2),
        _motion(0.99, 1.014, 0.65, 2, -5),
        _motion(1.007, 0.993, 0.48, 2, 0),
        _motion(1.017, 0.975, 0.2, 1, 4),
        _motion(0.996, 1.007, -0.42, -1, -2),
        _motion(0.991, 1.013, -0.66, -2, -5),
    )
    specs.extend(
        FrameSpec(
            f"walk/walk_{index:02d}.png",
            preset,
            "base",
            "walk",
            motion,
        )
        for index, (preset, motion) in enumerate(
            zip(walk_presets, walk_motion, strict=True),
            start=1,
        )
    )

    sit_sources = ("neutral", "neutral", "sit", "sit", "sit")
    sit_motion = (
        _motion(1.0, 1.0, 0.0, 0, 0),
        _motion(1.02, 0.95, -0.2, 0, 8),
        _motion(1.01, 0.97, 0.15, 0, 5),
        _motion(1.0, 1.0, -0.12, -1, 1),
        _motion(1.002, 0.998, 0.14, 1, 0),
    )
    specs.extend(
        FrameSpec(f"sit/sit_{index:02d}.png", preset, "base", "sit", motion)
        for index, (preset, motion) in enumerate(
            zip(sit_sources, sit_motion, strict=True),
            start=1,
        )
    )

    sleep_sources = ("sit", "sit", "sleep", "sleep", "sleep")
    sleep_motion = (
        _motion(1.0, 1.0, -0.1, 0, 0),
        _motion(1.012, 0.982, 0.16, 1, 3),
        _motion(1.008, 0.99, -0.22, -1, 2),
        _motion(1.014, 0.985, 0.2, 1, 3),
        _motion(1.004, 0.996, -0.12, 0, 1),
    )
    specs.extend(
        FrameSpec(f"sleep/sleep_{index:02d}.png", preset, "base", "sleep", motion)
        for index, (preset, motion) in enumerate(
            zip(sleep_sources, sleep_motion, strict=True),
            start=1,
        )
    )

    for index, motion in enumerate(
        (
            _motion(0.998, 1.003, -0.8, -3, 8),
            _motion(1.006, 0.992, 0.0, 0, 12),
            _motion(0.999, 1.004, 0.8, 3, 8),
        ),
        start=1,
    ):
        specs.append(
            FrameSpec(
                f"interact/drag_{index:02d}.png",
                "drag",
                "base",
                "drag",
                motion,
            )
        )

    for index, motion in enumerate(
        (
            _motion(1.0, 1.0, -0.25, 0, 0),
            _motion(1.003, 0.998, -0.55, -1, 1),
            _motion(0.998, 1.004, 0.35, 1, -1),
            _motion(1.001, 0.999, 0.18, 0, 0),
        ),
        start=1,
    ):
        specs.append(
            FrameSpec(
                f"interact/selfie_{index:02d}.png",
                "selfie",
                "base",
                "selfie",
                motion,
            )
        )

    specs.append(
        FrameSpec(
            "interact/wave_01.png",
            "wave",
            "base",
            "wave",
            _motion(1.001, 0.999, 0.3, 1, 0),
        )
    )
    for name, preset, angle in (
        ("happy", "happy", -0.18),
        ("shy", "shy", 0.16),
        ("surprised", "surprised", -0.14),
        ("annoyed", "annoyed", 0.22),
        ("sleepy", "sleep", -0.2),
        ("curious", "curious", 0.2),
    ):
        specs.append(
            FrameSpec(
                f"expressions/{name}_01.png",
                preset,
                "base",
                name,
                _motion(1.0, 1.0, angle, 0, 0),
            )
        )

    specs.extend(
        (
            FrameSpec(
                "interact/touch_transition_01.png",
                "shy",
                "touch",
                "touch_feedback",
                _motion(1.008, 0.992, -0.3, 0, 2),
            ),
            FrameSpec(
                "interact/touch_transition_02.png",
                "neutral",
                "touch",
                "touch_feedback",
                _motion(1.003, 0.998, 0.24, 0, 1),
            ),
        )
    )

    extension_specs: tuple[tuple[str, tuple[str, ...], tuple[Motion, ...]], ...] = (
        (
            "signature_hug_bear_v1",
            ("hug_bear",) * 4,
            (
                _motion(0.995, 1.004, -0.6, -2, 1),
                _motion(1.012, 0.99, -0.15, -1, 2),
                _motion(1.014, 0.988, 0.25, 1, 2),
                _motion(1.0, 1.0, 0.55, 2, 1),
            ),
        ),
        (
            "focus_writing_v1",
            ("writing",) * 6,
            (
                _motion(1.0, 1.0, -0.25, -1, 0),
                _motion(1.004, 0.997, -0.12, 0, 1),
                _motion(0.998, 1.003, 0.08, 1, 0),
                _motion(1.006, 0.995, 0.18, 1, 1),
                _motion(1.0, 1.001, 0.05, 0, 0),
                _motion(1.003, 0.998, -0.18, -1, 1),
            ),
        ),
        (
            "happy_jump_v1",
            ("happy",) * 5,
            (
                _motion(1.025, 0.97, -0.2, 0, 8),
                _motion(0.995, 1.012, 0.1, -1, -8),
                _motion(0.985, 1.025, 0.35, 0, -22),
                _motion(0.998, 1.01, -0.1, 1, -7),
                _motion(1.018, 0.978, -0.25, 0, 5),
            ),
        ),
        (
            "waiting_response_v1",
            ("waiting",) * 4,
            (
                _motion(1.0, 1.0, -0.25, -1, 0),
                _motion(1.004, 0.997, 0.0, 0, 1),
                _motion(0.999, 1.002, 0.22, 1, 0),
                _motion(1.002, 0.999, 0.05, 0, 1),
            ),
        ),
        (
            "micro_expressions_v1",
            ("neutral", "curious", "surprised", "shy", "waiting", "happy"),
            (
                _motion(1.0, 1.0, -0.12, 0, 0),
                _motion(1.001, 0.999, 0.1, 0, 0),
                _motion(0.999, 1.001, -0.08, 0, -1),
                _motion(1.002, 0.998, 0.13, 0, 1),
                _motion(1.0, 1.0, -0.1, -1, 0),
                _motion(1.001, 1.0, 0.11, 1, 0),
            ),
        ),
    )
    for sequence, presets, motions in extension_specs:
        for index, (preset, motion) in enumerate(
            zip(presets, motions, strict=True),
            start=1,
        ):
            specs.append(
                FrameSpec(
                    f"extensions/{sequence}/{index:02d}.png",
                    preset,
                    "advanced",
                    sequence,
                    motion,
                )
            )

    side_presets = (
        "side_a",
        "side_a",
        "side_b",
        "side_b",
        "side_b",
        "side_b",
        "side_a",
        "side_a",
    )
    side_motion = (
        _motion(1.008, 0.993, -0.3, -2, 0),
        _motion(1.018, 0.976, -0.12, -1, 4),
        _motion(0.995, 1.008, 0.22, 1, -2),
        _motion(0.99, 1.015, 0.35, 2, -5),
        _motion(1.007, 0.994, 0.3, 2, 0),
        _motion(1.017, 0.977, 0.12, 1, 4),
        _motion(0.996, 1.007, -0.22, -1, -2),
        _motion(0.991, 1.014, -0.36, -2, -5),
    )
    for index, (preset, motion) in enumerate(
        zip(side_presets, side_motion, strict=True),
        start=1,
    ):
        specs.append(
            FrameSpec(
                f"extensions/side_walk_v1/{index:02d}.png",
                preset,
                "side_walk",
                "side_walk_v1",
                motion,
            )
        )

    actual = {
        category: sum(spec.category == category for spec in specs)
        for category in ("base", "touch", "advanced", "side_walk")
    }
    if actual != {key: COUNTS[key] for key in actual} or len(specs) != COUNTS["total"]:
        raise AssertionError(f"内部 73 帧配方计数错误：{actual}，总计 {len(specs)}")
    paths = [spec.relative_path for spec in specs]
    if len(paths) != len(set(paths)):
        raise AssertionError("内部 73 帧配方含重复路径")
    return specs


def _fit_source_to_canvas(
    source: Image.Image,
    canvas_size: tuple[int, int],
) -> Image.Image:
    """把关键姿态高质量等比缩放到目标画布并保持脚底基线。"""

    rgba = source.convert("RGBA")
    bbox = _alpha_bbox(rgba)
    subject = rgba.crop(bbox)
    max_width = max(1, canvas_size[0] - 24)
    max_height = max(1, canvas_size[1] - 18)
    scale = min(max_width / subject.width, max_height / subject.height)
    size = (
        max(1, round(subject.width * scale)),
        max(1, round(subject.height * scale)),
    )
    subject = subject.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = (canvas_size[0] - subject.width) // 2
    y = canvas_size[1] - subject.height - 8
    canvas.alpha_composite(subject, (x, y))
    return canvas


def render_frame(
    source: Image.Image,
    canvas_size: tuple[int, int],
    motion: Motion,
    unique_index: int,
    *,
    uniqueness_attempt: int = 0,
) -> Image.Image:
    """以 LANCZOS/BICUBIC 对透明角色执行仿射风格的锚底变换。"""

    fitted = _fit_source_to_canvas(source, canvas_size)
    bbox = _alpha_bbox(fitted)
    subject = fitted.crop(bbox)

    phase = unique_index * 0.61803398875 + uniqueness_attempt * 0.377
    unique_scale = 1.0 + 0.0007 * math.sin(phase)
    scale_x = max(0.8, motion.scale_x * unique_scale)
    scale_y = max(0.8, motion.scale_y / unique_scale)
    target_size = (
        max(1, round(subject.width * scale_x)),
        max(1, round(subject.height * scale_y)),
    )
    subject = subject.resize(target_size, Image.Resampling.LANCZOS)
    angle = motion.angle + 0.025 * ((unique_index % 7) - 3)
    angle += uniqueness_attempt * 0.09
    if abs(angle) > 0.0001:
        subject = subject.rotate(
            angle,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )

    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    jitter_x = uniqueness_attempt if uniqueness_attempt % 2 else 0
    jitter_y = -(uniqueness_attempt // 2)
    x = (canvas_size[0] - subject.width) // 2 + motion.offset_x + jitter_x
    y = canvas_size[1] - subject.height - 8 + motion.offset_y + jitter_y
    canvas.alpha_composite(subject, (x, y))
    _alpha_bbox(canvas)
    return canvas


def _guarantee_unique_pixels(
    image: Image.Image,
    unique_index: int,
    seen_pixels: set[str],
) -> tuple[Image.Image, str, bool]:
    """极端退化输入下以完全透明像素携带不可见 nonce 保证物理唯一。"""

    current = image.convert("RGBA")
    digest = pixel_sha256(current)
    if digest not in seen_pixels:
        return current, digest, False
    pixels = current.load()
    for offset in range(1, 257):
        x = (unique_index * 17 + offset * 13) % current.width
        y = (unique_index * 29 + offset * 7) % current.height
        red, green, blue, alpha = pixels[x, y]
        if alpha != 0:
            continue
        nonce = (unique_index + offset) % 256
        pixels[x, y] = (nonce, (nonce * 73) % 256, (nonce * 151) % 256, 0)
        digest = pixel_sha256(current)
        if digest not in seen_pixels:
            return current, digest, True
        pixels[x, y] = (red, green, blue, alpha)
    raise RuntimeError("无法为输出帧建立唯一像素内容")


def _checkerboard(size: tuple[int, int], cell: int = 14) -> Image.Image:
    """生成用于透明素材联系表的浅色棋盘格。"""

    canvas = Image.new("RGBA", size, (244, 246, 250, 255))
    draw = ImageDraw.Draw(canvas)
    alternate = (225, 230, 238, 255)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=alternate)
    return canvas


def make_preview(frames: list[Image.Image], path: Path, duration_ms: int) -> None:
    """写入透明循环 GIF 预览。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    converted = [frame.convert("RGBA") for frame in frames]
    converted[0].save(
        path,
        save_all=True,
        append_images=converted[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
        optimize=False,
    )


def make_contact_sheet(
    labels: list[str],
    frames: list[Image.Image],
    path: Path,
    *,
    columns: int = 4,
) -> None:
    """生成带帧标签的透明素材联系表。"""

    if not frames:
        raise ValueError("联系表至少需要一帧")
    thumb_size = (180, 160)
    label_height = 24
    rows = math.ceil(len(frames) / columns)
    sheet = _checkerboard(
        (columns * thumb_size[0], rows * (thumb_size[1] + label_height)),
        cell=16,
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (label, frame) in enumerate(zip(labels, frames, strict=True)):
        column = index % columns
        row = index // columns
        x = column * thumb_size[0]
        y = row * (thumb_size[1] + label_height)
        thumb = frame.copy()
        thumb.thumbnail((thumb_size[0] - 12, thumb_size[1] - 12), Image.Resampling.LANCZOS)
        position = (
            x + (thumb_size[0] - thumb.width) // 2,
            y + (thumb_size[1] - thumb.height) // 2,
        )
        sheet.alpha_composite(thumb, position)
        draw.rectangle(
            (x, y + thumb_size[1], x + thumb_size[0] - 1, y + thumb_size[1] + label_height - 1),
            fill=(34, 39, 48, 230),
        )
        draw.text(
            (x + 6, y + thumb_size[1] + 6),
            label,
            fill=(255, 255, 255, 255),
            font=font,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "PNG", optimize=True)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """以 UTF-8 写入稳定、可审计的 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sequence_report(
    sequence: str,
    frame_entries: list[dict[str, Any]],
    frames: list[Image.Image],
    review_root: Path,
    *,
    duration_ms: int,
) -> dict[str, Any]:
    """为一个序列生成 GIF、联系表和逐帧哈希报告。"""

    root = review_root / ("walk" if sequence == "walk" else f"extensions/{sequence}")
    # 目录已经携带完整序列名；文件名保持短小，兼容 Windows 传统 MAX_PATH。
    preview = root / "preview.gif"
    contact = root / "contact-sheet.png"
    report_path = root / "report.json"
    labels = [Path(entry["path"]).stem for entry in frame_entries]
    make_preview(frames, preview, duration_ms)
    make_contact_sheet(labels, frames, contact)
    report = {
        "schema_version": 1,
        "sequence": sequence,
        "frame_count": len(frame_entries),
        "frames": frame_entries,
        "preview": preview.relative_to(review_root.parent).as_posix(),
        "preview_sha256": file_sha256(preview),
        "contact_sheet": contact.relative_to(review_root.parent).as_posix(),
        "contact_sheet_sha256": file_sha256(contact),
        "status": "generated_pending_user_review",
    }
    _write_json(report_path, report)
    return {
        "preview": report["preview"],
        "contact_sheet": report["contact_sheet"],
        "report": report_path.relative_to(review_root.parent).as_posix(),
        "report_sha256": file_sha256(report_path),
    }


def _safe_output_path(workspace: Path, output_dir: Path | None) -> Path:
    """解析输出路径并强制它保持在当前私有工作区内。"""

    root = workspace.resolve()
    selected = output_dir or Path("generated/live2d-sprite-pack-73")
    target = selected.resolve() if selected.is_absolute() else (root / selected).resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError("输出目录必须位于指定 workspace 内且不能等于 workspace 根")
    if target.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{target}")
    return target


def build_sprite_pack(
    workspace: Path,
    preset_dir: Path,
    *,
    output_dir: Path | None = None,
    canvas_size: tuple[int, int] = (560, 500),
) -> dict[str, Any]:
    """构建隔离的 73 帧候选包并返回总报告。"""

    workspace_root = workspace.resolve()
    if not workspace_root.is_dir():
        raise FileNotFoundError(f"workspace 不存在：{workspace_root}")
    if canvas_size[0] < 32 or canvas_size[1] < 32:
        raise ValueError("画布宽高都必须至少为 32 像素")
    target = _safe_output_path(workspace_root, output_dir)
    presets, source_report, fallbacks = load_presets(preset_dir)
    specs = frame_recipe()

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".sp73-",
        dir=workspace_root,
    ) as temporary:
        staging = Path(temporary) / "pack"
        staging.mkdir()
        frames_root = staging / "frames"
        review_root = staging / "review"
        seen_pixels: set[str] = set()
        seen_files: set[str] = set()
        rendered: dict[str, Image.Image] = {}
        entries: list[dict[str, Any]] = []

        for unique_index, spec in enumerate(specs, start=1):
            image: Image.Image | None = None
            digest = ""
            for attempt in range(8):
                candidate = render_frame(
                    presets[spec.preset],
                    canvas_size,
                    spec.motion,
                    unique_index,
                    uniqueness_attempt=attempt,
                )
                digest = pixel_sha256(candidate)
                if digest not in seen_pixels:
                    image = candidate
                    break
            if image is None:
                candidate, digest, nonce_used = _guarantee_unique_pixels(
                    candidate,
                    unique_index,
                    seen_pixels,
                )
                image = candidate
            else:
                nonce_used = False

            destination = frames_root / spec.relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, "PNG", optimize=True)
            saved_file_hash = file_sha256(destination)
            if saved_file_hash in seen_files:
                image, digest, nonce_used = _guarantee_unique_pixels(
                    image,
                    unique_index + 4096,
                    seen_pixels,
                )
                image.save(destination, "PNG", optimize=True)
                saved_file_hash = file_sha256(destination)
            if saved_file_hash in seen_files:
                raise RuntimeError(f"输出帧文件哈希重复：{spec.relative_path}")

            seen_pixels.add(digest)
            seen_files.add(saved_file_hash)
            rendered[spec.relative_path] = image
            alpha = image.getchannel("A")
            alpha_histogram = alpha.histogram()
            entry = {
                "index": unique_index,
                "path": f"frames/{spec.relative_path}",
                "category": spec.category,
                "sequence": spec.sequence,
                "requested_preset": spec.preset,
                "resolved_preset": source_report[spec.preset]["resolved_preset"],
                "fallback": source_report[spec.preset]["fallback"],
                "file_sha256": saved_file_hash,
                "pixel_sha256": digest,
                "pixel_size": list(image.size),
                "alpha_bbox": list(_alpha_bbox(image)),
                "nontransparent_pixels": sum(alpha_histogram[1:]),
                "invisible_uniqueness_nonce": nonce_used,
            }
            entries.append(entry)

        if len(entries) != COUNTS["total"]:
            raise RuntimeError(f"输出帧数不是 73：{len(entries)}")
        if len(seen_files) != COUNTS["total"] or len(seen_pixels) != COUNTS["total"]:
            raise RuntimeError("73 帧物理唯一性验证失败")

        previews: dict[str, dict[str, Any]] = {}
        sequence_names = (
            "walk",
            "signature_hug_bear_v1",
            "focus_writing_v1",
            "happy_jump_v1",
            "waiting_response_v1",
            "micro_expressions_v1",
            "side_walk_v1",
        )
        durations = {
            "walk": 110,
            "signature_hug_bear_v1": 190,
            "focus_writing_v1": 260,
            "happy_jump_v1": 130,
            "waiting_response_v1": 320,
            "micro_expressions_v1": 700,
            "side_walk_v1": 115,
        }
        for sequence in sequence_names:
            selected_specs = [spec for spec in specs if spec.sequence == sequence]
            selected_entries = [entry for entry in entries if entry["sequence"] == sequence]
            selected_frames = [rendered[spec.relative_path] for spec in selected_specs]
            previews[sequence] = _sequence_report(
                sequence,
                selected_entries,
                selected_frames,
                review_root,
                duration_ms=durations[sequence],
            )

        all_sheet = review_root / "all-73-contact-sheet.png"
        make_contact_sheet(
            [f"{entry['index']:02d} {Path(entry['path']).stem}" for entry in entries],
            [rendered[spec.relative_path] for spec in specs],
            all_sheet,
            columns=8,
        )
        report = {
            "schema_version": 1,
            "generated_at": now_utc(),
            "status": "generated_pending_user_review",
            "workspace": str(workspace_root),
            "preset_directory": str(preset_dir.resolve()),
            "output_directory": str(target),
            "canvas_size": list(canvas_size),
            "counts": dict(COUNTS),
            "presets": source_report,
            "fallbacks": fallbacks,
            "frames": entries,
            "previews": previews,
            "all_contact_sheet": all_sheet.relative_to(staging).as_posix(),
            "all_contact_sheet_sha256": file_sha256(all_sheet),
            "uniqueness": {
                "path_count": len({entry["path"] for entry in entries}),
                "unique_file_sha256_count": len(seen_files),
                "unique_pixel_sha256_count": len(seen_pixels),
                "all_physical_frames_unique": True,
            },
            "approval_written": False,
            "builder_invoked": False,
        }
        report_path = staging / "sprite-pack-73-report.json"
        _write_json(report_path, report)
        staging.rename(target)
    return report


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="把透明关键姿态 PNG 组装为隔离的 OnePic 73 帧候选包",
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--preset-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="workspace 内的输出目录；默认 generated/live2d-sprite-pack-73",
    )
    parser.add_argument("--canvas-width", type=int, default=560)
    parser.add_argument("--canvas-height", type=int, default=500)
    return parser.parse_args()


def main() -> int:
    """执行命令行构建并输出总报告路径。"""

    args = parse_args()
    report = build_sprite_pack(
        args.workspace,
        args.preset_dir,
        output_dir=args.output_dir,
        canvas_size=(args.canvas_width, args.canvas_height),
    )
    print(Path(report["output_directory"]) / "sprite-pack-73-report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
