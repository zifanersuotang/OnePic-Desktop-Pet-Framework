"""
本模块把已确认的独立桌宠工作区确定性地整理为 OnePic 私有运行素材。

职责范围：
- 读取 `user_assets/pets/<pet-id>/pet-config.json`；
- 使用一张标准角色、八种透明动作和八帧已确认走路生成 38 个运行帧；
- 生成动作九宫格、走路 GIF、坐下/入睡/醒来 GIF 和机器质量报告；
- 可选以已确认走路帧的中位透明像素面积为锚点，统一不同姿势的视觉体积；
- 可按宠物配置仅在透明轮廓边缘清理绿幕溢色，不修改审批绑定的源文件；
- 把追逐鼠标、陪打字、专注计时和招牌动作等通用陪伴配置写入运行清单；
- 写入默认双击快捷聊天 UI，并硬性保持打开窗口不联网；
- 校验并写入不依赖新增图片的可选养成玩法配置；
- 校验并写入纯本地动作台词池、随机概率、冷却和防重复配置；
- 校验并写入默认关闭的角色休息提醒计划，并只复制清单明确引用的 WAV 音效；
- 支持按宠物把不需要的基础表情永久回退到已批准状态，避免重建时旧素材回流；
- 可把已验收的多帧扩展动作、可选走路样式和八向视线确定性复制到运行目录，并按对应基础状态统一运行体型；
- 强制新项目显式区分默认精灵路线与用户明确选择的混合 Live2D 路线；
- 把经过校验的 `rendering_mode` 显式写入运行清单，供发布审计和跨平台降级判断；
- 校验并装配同一 Cubism 实例中的 RGBA 绑定配件清单及双耳鼻中轴约束（兼容旧常驻层）；
- 校验并写入每帧重施的 Live2D 固定外观参数，避免水印或造型被表情重置；
- 构建失败时尽力归档 staging；Windows 临时占用不得遮蔽原始素材诊断；
- 只有角色与走路/替代互动门禁均通过时才允许激活到 `user_assets/pet`；
- 对批准的静止/载具替代互动生成 `stationary` 占位序列，并为每次 runtime、QA
  和 latest-build 写入同一 build ID 与当前输入指纹；
- 激活前备份上一只桌宠，只复制运行帧和一张本地自拍成片。

本工具不生成角色图片、不访问网络、不复制原视频，也不替代用户的两次视觉确认。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from onepic_desktop_pet.care import normalize_care_config
from onepic_desktop_pet.break_scheduler import (
    canonical_break_reminder_manifest,
)
from onepic_desktop_pet.dialogue import normalize_dialogue_config
from onepic_desktop_pet.companion.framework import (
    normalize_companion_ui_config,
)
from onepic_desktop_pet.recipe import load_recipe
from onepic_desktop_pet.live2d_profile import (
    normalize_live2d_idle_animation,
    normalize_live2d_persistent_parameters,
    normalize_live2d_state_map,
)
from onepic_desktop_pet.workflow import (
    load_workflow,
    require_custom_pet_approved,
)
try:
    from .pet_workspace import (
        ACTION_NAMES,
        inspect_runtime_build_attestation,
        interaction_fallback_is_allowed,
        load_config,
        review_visual_evidence_sha256,
        runtime_asset_bundle_sha256,
        validate_required_touch_transitions,
        validate_workspace,
        validate_workflow_bindings,
        workspace_build_input_evidence,
        workspace_path,
    )
except ImportError:
    from pet_workspace import (
        ACTION_NAMES,
        inspect_runtime_build_attestation,
        interaction_fallback_is_allowed,
        load_config,
        review_visual_evidence_sha256,
        runtime_asset_bundle_sha256,
        validate_required_touch_transitions,
        validate_workspace,
        validate_workflow_bindings,
        workspace_build_input_evidence,
        workspace_path,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_ROOT = Path(
    os.environ.get("ONEPIC_USER_ROOT", PROJECT_ROOT / "user_assets")
).expanduser().resolve()
ACTIVE_PET_ROOT = USER_ROOT / "pet"
WORKFLOW_PATH = USER_ROOT / "workflow.json"
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
WALK_FACTORS = (0.45, 0.7, 1.2, 1.65, 0.45, 0.7, 1.2, 1.65)


def timestamp() -> str:
    """返回适合目录命名的 UTC 时间戳。"""

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def alpha_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    """返回可见像素边界，拒绝完全透明图片。"""

    bbox = image.getchannel("A").point(
        lambda value: 255 if value > 8 else 0
    ).getbbox()
    if bbox is None:
        raise ValueError("素材不包含可见像素")
    return bbox


def load_rgba(path: Path) -> Image.Image:
    """加载一张独立 RGBA 图片。"""

    if not path.is_file():
        raise FileNotFoundError(f"缺少输入素材：{path}")
    with Image.open(path) as source:
        return source.convert("RGBA").copy()


def normalize_pose(
    image: Image.Image,
    target_height: int,
    *,
    canvas_size: tuple[int, int],
    padding: int = 16,
    bottom_offset: int = 0,
    horizontal_offset: int = 0,
) -> Image.Image:
    """按可见区域缩放姿态并对齐统一底部基线。"""

    cropped = image.crop(alpha_bbox(image))
    maximum_width = canvas_size[0] - 2 * (
        padding + abs(horizontal_offset)
    )
    maximum_height = canvas_size[1] - padding * 2 - bottom_offset
    scale = min(
        target_height / cropped.height,
        maximum_width / cropped.width,
        maximum_height / cropped.height,
    )
    resized = cropped.resize(
        (
            max(1, round(cropped.width * scale)),
            max(1, round(cropped.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = (canvas_size[0] - resized.width) // 2 + horizontal_offset
    y = canvas_size[1] - padding - bottom_offset - resized.height
    canvas.alpha_composite(resized, (x, y))
    return canvas


def alpha_mass(image: Image.Image) -> float:
    """以透明度加权像素数衡量主体的视觉体积。"""

    histogram = image.getchannel("A").histogram()
    return sum(level * count for level, count in enumerate(histogram)) / 255.0


def normalize_pose_by_alpha_mass(
    image: Image.Image,
    target_mass: float,
    *,
    canvas_size: tuple[int, int],
    padding: int = 16,
    bottom_offset: int = 0,
    horizontal_offset: int = 0,
) -> Image.Image:
    """按透明像素面积统一视觉体积，同时维持底部基线并避免裁切。"""

    cropped = image.crop(alpha_bbox(image))
    source_mass = alpha_mass(cropped)
    if source_mass <= 0:
        raise ValueError("素材不包含可用于视觉体积归一化的像素")
    maximum_width = canvas_size[0] - 2 * (
        padding + abs(horizontal_offset)
    )
    maximum_height = canvas_size[1] - padding * 2 - bottom_offset
    scale = min(
        (target_mass / source_mass) ** 0.5,
        maximum_width / cropped.width,
        maximum_height / cropped.height,
    )
    resized = cropped.resize(
        (
            max(1, round(cropped.width * scale)),
            max(1, round(cropped.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = (canvas_size[0] - resized.width) // 2 + horizontal_offset
    y = canvas_size[1] - padding - bottom_offset - resized.height
    canvas.alpha_composite(resized, (x, y))
    return canvas


def maximum_fitted_alpha_mass(
    image: Image.Image,
    *,
    canvas_size: tuple[int, int],
    padding: int = 16,
    bottom_offset: int = 0,
    horizontal_offset: int = 0,
) -> float:
    """返回姿势在安全边距内等比放大后可达到的最大视觉体积。"""

    cropped = image.crop(alpha_bbox(image))
    maximum_width = canvas_size[0] - 2 * (
        padding + abs(horizontal_offset)
    )
    maximum_height = canvas_size[1] - padding * 2 - bottom_offset
    scale = min(
        maximum_width / cropped.width,
        maximum_height / cropped.height,
    )
    return alpha_mass(cropped) * scale * scale


def scaled_frame(
    frame: Image.Image,
    factor: float,
    *,
    canvas_size: tuple[int, int],
    padding: int = 16,
) -> Image.Image:
    """围绕画布底部中心缩放已规范姿态。"""

    cropped = frame.crop(alpha_bbox(frame))
    resized = cropped.resize(
        (
            max(1, round(cropped.width * factor)),
            max(1, round(cropped.height * factor)),
        ),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x = (canvas_size[0] - resized.width) // 2
    y = canvas_size[1] - padding - resized.height
    canvas.alpha_composite(resized, (x, y))
    return canvas


def scaled_frame_preserving_motion(
    frame: Image.Image,
    factor: float,
    *,
    canvas_size: tuple[int, int],
    source_baseline: int,
    target_padding: int = 16,
) -> Image.Image:
    """等比缩放扩展帧，同时保留相对脚底基线的跳跃和水平位移。"""

    bbox = alpha_bbox(frame)
    cropped = frame.crop(bbox)
    resized = cropped.resize(
        (
            max(1, round(cropped.width * factor)),
            max(1, round(cropped.height * factor)),
        ),
        Image.Resampling.LANCZOS,
    )
    canvas_width, canvas_height = canvas_size
    source_center_x = (bbox[0] + bbox[2]) / 2
    target_center_x = (
        canvas_width / 2
        + (source_center_x - canvas_width / 2) * factor
    )
    x = round(target_center_x - resized.width / 2)
    source_bottom_gap = max(0, source_baseline - bbox[3])
    target_baseline = canvas_height - target_padding
    target_bottom = target_baseline - round(source_bottom_gap * factor)
    y = target_bottom - resized.height
    if (
        x < 0
        or y < 0
        or x + resized.width > canvas_width
        or y + resized.height > canvas_height
    ):
        raise ValueError(
            "扩展动作尺寸归一化后超出安全画布："
            f"factor={factor:.4f}, bbox={bbox}"
        )
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    canvas.alpha_composite(resized, (x, y))
    return canvas


def save_frames(
    output_root: Path,
    directory: str,
    prefix: str,
    frames: list[Image.Image],
) -> list[str]:
    """保存运行帧并返回清单相对路径。"""

    paths: list[str] = []
    for index, frame in enumerate(frames, start=1):
        relative = f"{directory}/{prefix}_{index:02d}.png"
        destination = output_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame.save(destination, "PNG", optimize=True)
        paths.append(relative)
    return paths


def checkerboard(size: tuple[int, int]) -> Image.Image:
    """生成透明边缘检查用棋盘格。"""

    canvas = Image.new("RGBA", size, (238, 238, 238, 255))
    draw = ImageDraw.Draw(canvas)
    for y in range(0, size[1], 16):
        for x in range(0, size[0], 16):
            if (x // 16 + y // 16) % 2:
                draw.rectangle(
                    (x, y, x + 15, y + 15),
                    fill=(205, 205, 205, 255),
                )
    return canvas


def make_action_sheet(poses: dict[str, Image.Image], path: Path) -> None:
    """生成当前启用核心姿态的视觉检查表。"""

    labels = tuple(
        label
        for label in (
            "stand",
            "wave",
            "happy",
            "selfie",
            "surprised",
            "annoyed",
            "sit",
            "sleep",
            "drag",
        )
        if label in poses
    )
    cell_width, cell_height = 360, 330
    rows = max(1, (len(labels) + 2) // 3)
    sheet = checkerboard((cell_width * 3, cell_height * rows))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, label in enumerate(labels):
        preview = poses[label].copy()
        preview.thumbnail(
            (cell_width - 24, cell_height - 44),
            Image.Resampling.LANCZOS,
        )
        left = index % 3 * cell_width
        top = index // 3 * cell_height
        x = left + (cell_width - preview.width) // 2
        y = top + 22 + (cell_height - 44 - preview.height) // 2
        sheet.alpha_composite(preview, (x, y))
        draw.rectangle(
            (left, top, left + cell_width - 1, top + 21),
            fill=(32, 32, 35, 255),
        )
        draw.text((left + 8, top + 5), label, fill="white", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".png":
        sheet.save(path, "PNG", optimize=True)
    else:
        sheet.convert("RGB").save(path, "JPEG", quality=94)


def make_animation_preview(
    frames: list[Image.Image],
    path: Path,
    *,
    canvas_size: tuple[int, int],
    duration: int | list[int],
) -> None:
    """把透明帧放在浅色地面上生成 GIF。"""

    previews: list[Image.Image] = []
    ground = canvas_size[1] - 16
    for frame in frames:
        canvas = Image.new("RGBA", canvas_size, (247, 247, 244, 255))
        draw = ImageDraw.Draw(canvas)
        draw.line(
            (20, ground, canvas_size[0] - 20, ground),
            fill=(190, 166, 150, 255),
            width=2,
        )
        canvas.alpha_composite(frame)
        previews.append(canvas.convert("RGB"))
    path.parent.mkdir(parents=True, exist_ok=True)
    previews[0].save(
        path,
        save_all=True,
        append_images=previews[1:],
        duration=duration,
        loop=0,
        optimize=False,
    )


def count_green_pixels(image: Image.Image) -> int:
    """统计仍疑似属于绿幕残留的可见像素。"""

    count = 0
    for red, green, blue, alpha in image.get_flattened_data():
        if (
            alpha > 24
            and green > 180
            and green > red * 1.45
            and green > blue * 1.45
        ):
            count += 1
    return count


def despill_green_edges(
    image: Image.Image,
    *,
    edge_radius: int = 4,
) -> Image.Image:
    """仅清理透明轮廓附近的绿幕溢色，保留主体内部的真实绿色。

    该操作只用于构建 staging 中的派生帧。审批绑定的候选图和动作源图保持
    原样；显式配置未启用时不会调用本函数。
    """

    if edge_radius < 1 or edge_radius > 8:
        raise ValueError("source_cleanup.edge_radius 必须在 1–8 之间")
    source = image.convert("RGBA")
    alpha = source.getchannel("A")
    eroded = alpha.filter(ImageFilter.MinFilter(edge_radius * 2 + 1))
    edge = ImageChops.subtract(alpha, eroded)
    cleaned = source.copy()
    source_pixels = source.load()
    edge_pixels = edge.load()
    cleaned_pixels = cleaned.load()
    for y in range(source.height):
        for x in range(source.width):
            red, green, blue, opacity = source_pixels[x, y]
            if (
                edge_pixels[x, y] > 0
                and opacity > 24
                and green > 180
                and green > red * 1.45
                and green > blue * 1.45
            ):
                cleaned_pixels[x, y] = (
                    red,
                    max(red, blue),
                    blue,
                    opacity,
                )
    return cleaned


def _alpha_centroid(image: Image.Image) -> tuple[float, float]:
    """返回 alpha 加权质心。"""

    alpha = image.getchannel("A")
    total = alpha_mass(image)
    if total <= 0:
        raise ValueError("无法计算透明图片质心")
    x_total = 0.0
    y_total = 0.0
    pixels = alpha.load()
    for y in range(alpha.height):
        for x in range(alpha.width):
            weight = pixels[x, y] / 255.0
            x_total += x * weight
            y_total += y * weight
    return x_total / total, y_total / total


def _aligned_shape_change(left: Image.Image, right: Image.Image) -> float:
    """去除整体平移后估算两帧轮廓变化比例。"""

    left_center = _alpha_centroid(left)
    right_center = _alpha_centroid(right)
    shift_x = round(left_center[0] - right_center[0])
    shift_y = round(left_center[1] - right_center[1])
    aligned = Image.new("L", right.size, 0)
    aligned.paste(right.getchannel("A"), (shift_x, shift_y))
    difference = ImageChops.difference(left.getchannel("A"), aligned)
    changed_mass = sum(
        level * count for level, count in enumerate(difference.histogram())
    ) / 255.0
    denominator = max(1.0, (alpha_mass(left) + alpha_mass(right)) / 2)
    return changed_mass / denominator


def build_walk_quality_report(
    frames: list[Image.Image],
    config: dict[str, Any],
) -> dict[str, Any]:
    """检查共用缩放、身体起伏、步态变化和首尾循环接缝。"""

    if len(frames) != 8:
        return {"status": "failed", "failures": ["走路必须恰好 8 帧"]}
    boxes = [alpha_bbox(frame) for frame in frames]
    centroids = [_alpha_centroid(frame) for frame in frames]
    shape_changes = [
        _aligned_shape_change(current, following)
        for current, following in zip(frames, frames[1:] + frames[:1], strict=True)
    ]
    non_seam = shape_changes[:-1]
    median_change = median(non_seam)
    body_rise = max(box[1] for box in boxes) - min(box[1] for box in boxes)
    bottom_rise = max(box[3] for box in boxes) - min(box[3] for box in boxes)
    minimum_change = float(config.get("minimum_shape_change_ratio", 0.012))
    minimum_rise = int(config.get("minimum_body_rise_pixels", 2))
    seam_limit = float(config.get("maximum_loop_seam_ratio", 2.0))
    failures: list[str] = []
    translation_only = [
        index + 1
        for index, ratio in enumerate(shape_changes)
        if ratio < minimum_change
    ]
    if translation_only:
        failures.append(
            "走路存在疑似整体平移帧对："
            + ", ".join(str(index) for index in translation_only)
        )
    if max(body_rise, bottom_rise) < minimum_rise:
        failures.append(
            f"身体起伏不足：顶部/脚底最大变化 {body_rise}/{bottom_rise}px"
        )
    if median_change > 0 and shape_changes[-1] > median_change * seam_limit:
        failures.append(
            "第 8 帧回到第 1 帧的接缝变化过大："
            f"{shape_changes[-1]:.4f} > {median_change * seam_limit:.4f}"
        )
    widths = [box[2] - box[0] for box in boxes]
    if min(widths) <= 0 or max(widths) / min(widths) > 1.8:
        failures.append("走路朝向/轮廓宽度变化异常，需人工复核朝向一致性")
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "shape_change_ratios": [round(value, 6) for value in shape_changes],
        "centroids": [[round(x, 2), round(y, 2)] for x, y in centroids],
        "body_top_rise_pixels": body_rise,
        "foot_bottom_rise_pixels": bottom_rise,
        "loop_seam_ratio": (
            round(shape_changes[-1] / median_change, 4)
            if median_change
            else None
        ),
        "shared_scale_required": True,
        "manual_checks": [
            "所有帧朝向一致",
            "左右落脚交替且步幅自然",
            "手臂、腿和重心发生形变，不是整体平移",
        ],
    }


def build_quality_report(
    manifest: dict[str, Any],
    output_root: Path,
    *,
    canvas_size: tuple[int, int],
    expected_masses: dict[str, float] | None = None,
    visual_mass: dict[str, Any] | None = None,
    extra_frames: list[str] | None = None,
    additional_frames: list[tuple[str, str]] | None = None,
    walk_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """验证每个运行帧的画布、透明角、留白和绿幕残留。"""

    failures: list[str] = []
    frames: list[dict[str, Any]] = []
    all_frames = [
        (state, relative)
        for state, relatives in manifest["animations"].items()
        for relative in relatives
    ]
    all_frames.extend(
        ("touch_transition", relative) for relative in (extra_frames or [])
    )
    all_frames.extend(additional_frames or [])
    static_tolerance = float((visual_mass or {}).get("static_tolerance_ratio", 0.12))
    transition_tolerance = float(
        (visual_mass or {}).get("transition_tolerance_ratio", 0.22)
    )
    for state, relative in all_frames:
        image = load_rgba(output_root / relative)
        bbox = alpha_bbox(image)
        corners = (
            image.getpixel((0, 0))[3],
            image.getpixel((image.width - 1, 0))[3],
            image.getpixel((0, image.height - 1))[3],
            image.getpixel((image.width - 1, image.height - 1))[3],
        )
        margin = min(
            bbox[0],
            bbox[1],
            image.width - bbox[2],
            image.height - bbox[3],
        )
        green_pixels = count_green_pixels(image)
        mass = alpha_mass(image)
        expected = (expected_masses or {}).get(relative)
        deviation = abs(mass - expected) / expected if expected else None
        tolerance = (
            transition_tolerance
            if state in {"sit", "sleep"}
            else static_tolerance
        )
        if image.size != canvas_size:
            failures.append(f"{relative}: 画布尺寸错误")
        if max(corners) != 0:
            failures.append(f"{relative}: 透明角异常")
        if margin < 5:
            failures.append(f"{relative}: 内容接近裁切边缘")
        if green_pixels > 20:
            failures.append(f"{relative}: 疑似绿幕残留 {green_pixels}")
        if deviation is not None and deviation > tolerance:
            failures.append(
                f"{relative}: alpha mass 偏差 {deviation:.1%} "
                f"超过阈值 {tolerance:.1%}（实际 {mass:.1f}，目标 {expected:.1f}）"
            )
        frames.append(
            {
                "state": state,
                "path": relative,
                "size": list(image.size),
                "bbox": list(bbox),
                "minimum_margin": margin,
                "transparent_corners": max(corners) == 0,
                "green_dominant_pixels": green_pixels,
                "alpha_mass": round(mass, 2),
                "expected_alpha_mass": round(expected, 2) if expected else None,
                "alpha_mass_deviation_ratio": (
                    round(deviation, 6) if deviation is not None else None
                ),
                "alpha_mass_tolerance_ratio": tolerance if expected else None,
            }
        )
    if walk_report and walk_report.get("status") != "passed":
        failures.extend(
            f"walk: {failure}" for failure in walk_report.get("failures", [])
        )
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "frame_count": len(frames),
        "frames": frames,
        "visual_mass_contract": visual_mass or {},
        "walk_qa": walk_report or {},
        "manual_checks": [
            "九种核心动作是同一人物，脸型、发型、服装、鞋和配饰一致",
            "每个动作只有一个头、两只手、两条腿，手指和关节自然",
            "头发、手脚、武器、翅膀或其他突出配饰没有被裁切",
            "走路有腿部交替、身体起伏和手臂摆动，不是整体平移",
            "惊讶表情克制，独立感叹号由程序绘制",
            "自拍展示来自 source/selfie.png，不使用生成图代替",
        ],
    }


EXTENSION_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,47}$")
BASE_STATE_NAMES = {
    "idle",
    "walk",
    "sit",
    "wave",
    "happy",
    "shy",
    "surprised",
    "annoyed",
    "sleepy",
    "curious",
}
DIRECTION_NAMES = {
    "center",
    "north",
    "north_east",
    "east",
    "south_east",
    "south",
    "south_west",
    "west",
    "north_west",
}


def resolve_private_source(
    workspace: Path,
    relative: object,
    *,
    label: str,
) -> Path:
    """把扩展素材限制在当前宠物工作区内，拒绝绝对路径和目录穿越。"""

    value = Path(str(relative))
    if value.is_absolute():
        raise ValueError(f"{label} 必须使用工作区相对路径")
    workspace_root = workspace.resolve()
    path = (workspace_root / value).resolve()
    if not path.is_relative_to(workspace_root):
        raise ValueError(f"{label} 不能离开当前宠物工作区")
    if not path.is_file():
        raise FileNotFoundError(f"缺少{label}：{path}")
    return path


def stage_extension_animations(
    workspace: Path,
    config: dict[str, Any],
    runtime_stage: Path,
    *,
    canvas_size: tuple[int, int],
    base_state_heights: dict[str, int],
) -> tuple[dict[str, Any], list[tuple[str, str]], dict[str, Any]]:
    """装配配置化扩展动作，并可统一体型且保留跳跃相对高度。"""

    raw = config.get("extension_animations", {})
    if not isinstance(raw, dict):
        raise ValueError("extension_animations 必须是 JSON 对象")
    normalization = config.get("extension_size_normalization", {})
    if not isinstance(normalization, dict):
        raise ValueError("extension_size_normalization 必须是 JSON 对象")
    normalization_enabled = bool(normalization.get("enabled", False))
    normalization_mode = str(
        normalization.get("mode", "base_state_height")
    )
    if normalization_enabled and normalization_mode != "base_state_height":
        raise ValueError(
            "extension_size_normalization.mode 只支持 base_state_height"
        )
    target_padding = int(normalization.get("target_padding", 16))
    if not 8 <= target_padding <= 48:
        raise ValueError(
            "extension_size_normalization.target_padding 必须为 8–48"
        )
    minimum_scale = float(normalization.get("minimum_scale", 0.6))
    maximum_scale = float(normalization.get("maximum_scale", 1.4))
    if not 0.25 <= minimum_scale <= 1.0:
        raise ValueError(
            "extension_size_normalization.minimum_scale 必须为 0.25–1.0"
        )
    if not 1.0 <= maximum_scale <= 2.0 or minimum_scale > maximum_scale:
        raise ValueError(
            "extension_size_normalization.maximum_scale 必须为 1.0–2.0"
        )
    manifest: dict[str, Any] = {}
    qa: dict[str, Any] = {}
    additional: list[tuple[str, str]] = []
    for name, value in raw.items():
        animation_name = str(name)
        if not EXTENSION_NAME_PATTERN.fullmatch(animation_name):
            raise ValueError(f"扩展动作名称不合法：{animation_name}")
        if not isinstance(value, dict):
            raise ValueError(f"extension_animations.{animation_name} 必须是对象")
        if not value.get("enabled", False):
            continue
        source_frames = value.get("frames", [])
        if not isinstance(source_frames, list) or not 1 <= len(source_frames) <= 32:
            raise ValueError(f"扩展动作 {animation_name} 帧数必须为 1–32")
        walk_variant = bool(value.get("walk_variant", False))
        if walk_variant and len(source_frames) != 8:
            raise ValueError(
                f"走路样式 {animation_name} 必须正好包含 8 帧"
            )
        duration_ms = int(value.get("frame_duration_ms", 180))
        if not 40 <= duration_ms <= 5000:
            raise ValueError(f"扩展动作 {animation_name} 单帧时长必须为 40–5000 ms")
        base_state = str(value.get("base_state", "idle"))
        if base_state not in BASE_STATE_NAMES:
            raise ValueError(f"扩展动作 {animation_name} base_state 不受支持")
        runtime_paths: list[str] = []
        source_hashes: list[str] = []
        source_images: list[Image.Image] = []
        for index, source_relative in enumerate(source_frames, start=1):
            source_path = resolve_private_source(
                workspace,
                source_relative,
                label=f"扩展动作 {animation_name} 第 {index} 帧",
            )
            image = load_rgba(source_path)
            if image.size != canvas_size:
                raise ValueError(
                    f"扩展动作 {animation_name} 第 {index} 帧画布必须为 "
                    f"{canvas_size[0]}×{canvas_size[1]}"
                )
            source_images.append(image)
            source_hashes.append(_sha256(source_path))

        size_normalization: dict[str, Any] | None = None
        normalize_this_animation = normalization_enabled and bool(
            value.get("normalize_runtime_size", True)
        )
        runtime_images = source_images
        if normalize_this_animation:
            reference_state = str(
                value.get("size_reference_state", base_state)
            )
            target_height = base_state_heights.get(reference_state)
            if target_height is None:
                raise ValueError(
                    f"扩展动作 {animation_name} 的尺寸参考状态不存在："
                    f"{reference_state}"
                )
            source_heights = [
                alpha_bbox(image)[3] - alpha_bbox(image)[1]
                for image in source_images
            ]
            source_median_height = float(median(source_heights))
            scale_factor = target_height / source_median_height
            if not minimum_scale <= scale_factor <= maximum_scale:
                raise ValueError(
                    f"扩展动作 {animation_name} 尺寸缩放 {scale_factor:.4f} "
                    f"超出 {minimum_scale:.2f}–{maximum_scale:.2f}"
                )
            source_baseline = max(
                alpha_bbox(image)[3] for image in source_images
            )
            runtime_images = [
                scaled_frame_preserving_motion(
                    image,
                    scale_factor,
                    canvas_size=canvas_size,
                    source_baseline=source_baseline,
                    target_padding=target_padding,
                )
                for image in source_images
            ]
            runtime_heights = [
                alpha_bbox(image)[3] - alpha_bbox(image)[1]
                for image in runtime_images
            ]
            size_normalization = {
                "enabled": True,
                "mode": normalization_mode,
                "reference_state": reference_state,
                "target_subject_height": target_height,
                "source_median_subject_height": round(
                    source_median_height,
                    2,
                ),
                "runtime_median_subject_height": round(
                    float(median(runtime_heights)),
                    2,
                ),
                "scale_factor": round(scale_factor, 6),
                "preserved_motion_offset": True,
            }

        for index, image in enumerate(runtime_images, start=1):
            relative = f"extensions/{animation_name}/{index:02d}.png"
            destination = runtime_stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, "PNG", optimize=True)
            runtime_paths.append(relative)
            additional.append((f"extension:{animation_name}", relative))
        manifest[animation_name] = {
            "frames": runtime_paths,
            "frame_duration_ms": duration_ms,
            "loop": bool(value.get("loop", False)),
            "base_state": base_state,
            "hold_last_ms": max(0, int(value.get("hold_last_ms", 0))),
            "menu_label": (
                str(value.get("menu_label"))
                if value.get("menu_label")
                else None
            ),
            "walk_variant": walk_variant,
            "walk_variant_label": (
                str(value.get("walk_variant_label"))
                if value.get("walk_variant_label")
                else None
            ),
            "walk_variant_default": bool(
                value.get("walk_variant_default", False)
            ),
            "mirror_with_direction": bool(
                value.get("mirror_with_direction", True)
            ),
        }
        if size_normalization is not None:
            manifest[animation_name]["size_normalization"] = size_normalization
        qa[animation_name] = {
            "status": "passed",
            "frame_count": len(runtime_paths),
            "source_sha256": source_hashes,
            "walk_variant": walk_variant,
        }
        if size_normalization is not None:
            qa[animation_name]["size_normalization"] = size_normalization
    return manifest, additional, qa


def stage_directional_mouse_follow(
    workspace: Path,
    config: dict[str, Any],
    runtime_stage: Path,
    *,
    canvas_size: tuple[int, int],
) -> tuple[dict[str, Any] | None, list[tuple[str, str]], dict[str, Any]]:
    """复制八向视线素材并把源路径替换为运行目录相对路径。"""

    raw = config.get("directional_mouse_follow", {})
    if not isinstance(raw, dict):
        raise ValueError("directional_mouse_follow 必须是 JSON 对象")
    if not raw.get("enabled", False):
        return None, [], {"status": "disabled", "frame_count": 0}
    frames = raw.get("frames", {})
    if not isinstance(frames, dict) or "center" not in frames:
        raise ValueError("directional_mouse_follow.frames 必须包含 center")
    unknown = set(str(name) for name in frames) - DIRECTION_NAMES
    if unknown:
        raise ValueError(f"不支持的视线方向：{sorted(unknown)}")
    runtime_frames: dict[str, str] = {}
    additional: list[tuple[str, str]] = []
    source_hashes: dict[str, str] = {}
    for name, source_relative in frames.items():
        direction = str(name)
        source_path = resolve_private_source(
            workspace,
            source_relative,
            label=f"视线方向 {direction}",
        )
        image = load_rgba(source_path)
        if image.size != canvas_size:
            raise ValueError(
                f"视线方向 {direction} 画布必须为 "
                f"{canvas_size[0]}×{canvas_size[1]}"
            )
        relative = f"directional/{direction}.png"
        destination = runtime_stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "PNG", optimize=True)
        runtime_frames[direction] = relative
        source_hashes[direction] = _sha256(source_path)
        additional.append(("directional_gaze", relative))
    manifest = {
        "enabled": True,
        "frames": runtime_frames,
        "poll_interval_ms": min(
            500,
            max(40, int(raw.get("poll_interval_ms", 70))),
        ),
        "dead_zone_px": max(8, int(raw.get("dead_zone_px", 48))),
        "switch_delay_ms": max(0, int(raw.get("switch_delay_ms", 105))),
    }
    qa = {
        "status": "passed",
        "frame_count": len(runtime_frames),
        "source_sha256": source_hashes,
    }
    return manifest, additional, qa


def backup_directory(path: Path, backup_parent: Path, label: str) -> Path | None:
    """在同一受控根目录内把现有目录改名备份。"""

    if not path.exists():
        return None
    backup_parent.mkdir(parents=True, exist_ok=True)
    destination = backup_parent / f"{label}-{timestamp()}"
    if destination.exists():
        raise FileExistsError(f"备份目录已存在：{destination}")
    path.rename(destination)
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_hybrid_runtime(
    workspace: Path,
    config: dict[str, Any],
    runtime_stage: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """把已导出的 Live2D SDK 文件放入 staging，并校验所有本地引用。"""

    rendering_mode = str(config.get("rendering_mode", "legacy-auto")).strip()
    if rendering_mode not in {"sprite", "hybrid-live2d", "legacy-auto"}:
        raise ValueError(f"不支持的 rendering_mode：{rendering_mode}")
    raw = config.get("hybrid_runtime", {})
    configured = isinstance(raw, dict) and bool(raw.get("enabled"))
    if rendering_mode == "sprite" and configured:
        raise ValueError(
            "基础款 rendering_mode=sprite 禁止启用 hybrid_runtime；"
            "只有用户明确要求 Live2D 后才能改为 hybrid-live2d"
        )
    if rendering_mode == "hybrid-live2d" and not configured:
        raise ValueError(
            "rendering_mode=hybrid-live2d 需要完整且 enabled=true 的 hybrid_runtime"
        )
    if not configured:
        return None, {"status": "not-configured", "files": []}
    if raw.get("idle_backend") != "live2d":
        raise ValueError("hybrid_runtime.enabled=true 时 idle_backend 必须为 live2d")
    load_timeout_ms = raw.get("load_timeout_ms", 30_000)
    if (
        isinstance(load_timeout_ms, bool)
        or not isinstance(load_timeout_ms, int)
        or not 5_000 <= load_timeout_ms <= 120_000
    ):
        raise ValueError(
            "hybrid_runtime.load_timeout_ms 必须是 5000–120000 的整数"
        )

    workspace_root = workspace.resolve()
    source_root = (
        workspace / str(raw.get("source_directory", "live2d-runtime"))
    ).resolve()
    if source_root != workspace_root and workspace_root not in source_root.parents:
        raise ValueError("Live2D source_directory 不能离开宠物工作区")
    if not source_root.is_dir():
        raise FileNotFoundError(f"缺少 Live2D 运行时目录：{source_root}")

    model_name = str(raw.get("model_file", "")).strip()
    if not model_name:
        raise ValueError("启用混合运行时后必须声明 hybrid_runtime.model_file")
    model_path = (source_root / model_name).resolve()
    if source_root not in model_path.parents or not model_path.is_file():
        raise FileNotFoundError(f"Live2D model_file 无效：{model_path}")
    model = json.loads(model_path.read_text(encoding="utf-8-sig"))
    references = model.get("FileReferences", {})
    expression_ids = {
        str(expression.get("Name", "")).strip()
        for expression in references.get("Expressions", [])
        if isinstance(expression, dict) and str(expression.get("Name", "")).strip()
    }
    motion_keys = {
        (str(group), index)
        for group, motions in references.get("Motions", {}).items()
        for index, motion in enumerate(motions if isinstance(motions, list) else [])
        if isinstance(motion, dict)
    }
    live2d_state_map = normalize_live2d_state_map(
        raw.get("live2d_state_map")
    )
    base_state_names = {
        "idle",
        "walk",
        "sit",
        "sleep",
        "drag",
        "selfie",
        "wave",
        "happy",
        "shy",
        "surprised",
        "annoyed",
        "sleepy",
        "curious",
    }
    extension_names = {
        f"extension:{name}"
        for name, entry in config.get("extension_animations", {}).items()
        if isinstance(entry, dict) and entry.get("enabled", True)
    }
    unknown_state_names = sorted(
        set(live2d_state_map) - base_state_names - extension_names
    )
    if unknown_state_names:
        raise ValueError(
            "live2d_state_map 含未知状态：" + ", ".join(unknown_state_names)
        )
    for state_name, presentation in live2d_state_map.items():
        requested_expressions = []
        if presentation.get("expression"):
            requested_expressions.append(presentation["expression"])
        requested_expressions.extend(presentation.get("expressions", []))
        missing_expressions = sorted(
            set(requested_expressions) - expression_ids
        )
        if missing_expressions:
            raise ValueError(
                f"live2d_state_map.{state_name} 引用了模型未登记的表情："
                + ", ".join(missing_expressions)
            )
        motion = presentation.get("motion")
        if motion and (motion["group"], motion["no"]) not in motion_keys:
            raise ValueError(
                f"live2d_state_map.{state_name} 引用了模型未登记的动作："
                f"{motion['group']}:{motion['no']}"
            )
    referenced = [
        references.get("Moc"),
        *references.get("Textures", []),
    ]
    for key in ("Physics", "Pose", "DisplayInfo", "UserData"):
        if references.get(key):
            referenced.append(references[key])
    for expression in references.get("Expressions", []):
        if isinstance(expression, dict):
            referenced.append(expression.get("File"))
    for motions in references.get("Motions", {}).values():
        for motion in motions if isinstance(motions, list) else []:
            if not isinstance(motion, dict):
                continue
            referenced.append(motion.get("File"))
            if motion.get("Sound"):
                referenced.append(motion["Sound"])
    checked: list[Path] = [model_path]
    for relative in referenced:
        value = str(relative or "").strip()
        path = (source_root / value).resolve()
        if not value or source_root not in path.parents or not path.is_file():
            raise FileNotFoundError(f"Live2D 模型引用缺失或越界：{relative}")
        checked.append(path)

    layer_manifest_value = str(
        raw.get("layer_manifest_file", "")
    ).strip()
    accessory_manifest_value = str(
        raw.get("accessory_manifest_file", "")
    ).strip()
    if layer_manifest_value and accessory_manifest_value:
        raise ValueError(
            "Cubism 常驻配件不能与分层 WebGL 后端同时启用"
        )
    layer_manifest_relative: str | None = None
    layered_roles: list[str] = []
    if layer_manifest_value:
        layer_manifest_path = (source_root / layer_manifest_value).resolve()
        if (
            source_root not in layer_manifest_path.parents
            or not layer_manifest_path.is_file()
        ):
            raise FileNotFoundError(
                f"Live2D 分层清单无效：{layer_manifest_path}"
            )
        layer_manifest = json.loads(
            layer_manifest_path.read_text(encoding="utf-8-sig")
        )
        canvas_size = layer_manifest.get("canvas_size")
        layers = layer_manifest.get("layers")
        if (
            layer_manifest.get("schema_version") != 1
            or not isinstance(canvas_size, list)
            or len(canvas_size) != 2
            or any(
                not isinstance(item, int) or item <= 0
                for item in canvas_size
            )
            or not isinstance(layers, list)
            or not layers
        ):
            raise ValueError("Live2D 分层清单结构无效")
        checked.append(layer_manifest_path)
        roles: set[str] = set()
        for index, layer in enumerate(layers):
            if not isinstance(layer, dict):
                raise ValueError(f"Live2D 分层 layers[{index}] 必须是对象")
            role = str(layer.get("role", "")).strip()
            file_value = str(layer.get("file", "")).strip()
            pivot = layer.get("pivot")
            if (
                not role
                or role in roles
                or not file_value
                or not isinstance(pivot, list)
                or len(pivot) != 2
                or any(not isinstance(item, int) for item in pivot)
            ):
                raise ValueError(f"Live2D 分层 layers[{index}] 无效")
            roles.add(role)
            layer_path = (
                layer_manifest_path.parent / file_value
            ).resolve()
            if (
                source_root not in layer_path.parents
                or not layer_path.is_file()
            ):
                raise FileNotFoundError(
                    f"Live2D 分层图片缺失或越界：{file_value}"
                )
            with Image.open(layer_path) as image:
                if list(image.size) != canvas_size or image.mode != "RGBA":
                    raise ValueError(
                        f"Live2D 分层图片必须是 {canvas_size} RGBA："
                        f"{file_value}"
                    )
            checked.append(layer_path)
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
                "Live2D 分层缺少角色：" + ", ".join(missing_roles)
            )
        layered_roles = sorted(roles)
        layer_manifest_relative = (
            "live2d/"
            + layer_manifest_path.relative_to(source_root).as_posix()
        )

    accessory_manifest_relative: str | None = None
    accessory_roles: list[str] = []
    accessory_binding_mode: str | None = None
    accessory_render_layer: str | None = None
    accessory_ear_pair_center: str | None = None
    if accessory_manifest_value:
        accessory_manifest_path = (
            source_root / accessory_manifest_value
        ).resolve()
        if (
            source_root not in accessory_manifest_path.parents
            or not accessory_manifest_path.is_file()
        ):
            raise FileNotFoundError(
                f"Live2D 常驻配件清单无效：{accessory_manifest_path}"
            )
        accessory_manifest = json.loads(
            accessory_manifest_path.read_text(encoding="utf-8-sig")
        )
        accessory_layers = accessory_manifest.get("layers")
        accessory_schema = accessory_manifest.get("schema_version")
        if (
            accessory_schema not in {1, 2}
            or not isinstance(accessory_layers, list)
            or not accessory_layers
        ):
            raise ValueError("Live2D 常驻配件清单结构无效")
        checked.append(accessory_manifest_path)
        if accessory_schema == 2:
            binding = accessory_manifest.get("binding")
            accessory_binding_mode = str(
                binding.get("mode", "") if isinstance(binding, dict) else ""
            ).strip()
            accessory_render_layer = str(
                accessory_manifest.get("render_layer", "")
            ).strip()
            if (
                not isinstance(binding, dict)
                or accessory_binding_mode != "cubism_part_mesh_affine"
                or accessory_render_layer != "same_model_overlay"
            ):
                raise ValueError(
                    "Live2D 绑定配件必须使用同模型 Cubism Part 网格绑定"
                )
            head_part_id = str(binding.get("head_part_id", "")).strip()
            nose_part_id = str(binding.get("nose_part_id", "")).strip()
            model_moc_sha256 = str(
                binding.get("model_moc_sha256", "")
            ).strip().lower()
            cdi_file = str(binding.get("cdi_file", "")).strip()
            cdi_sha256 = str(binding.get("cdi_sha256", "")).strip().lower()
            raw_ear_pair_center = binding.get("ear_pair_center")
            if raw_ear_pair_center is not None:
                accessory_ear_pair_center = str(raw_ear_pair_center).strip()
                if accessory_ear_pair_center != "nose":
                    raise ValueError(
                        "Live2D 双耳中轴只能绑定到实时 nose Part"
                    )
            if (
                not head_part_id
                or not nose_part_id
                or head_part_id == nose_part_id
                or re.fullmatch(r"[0-9a-f]{64}", model_moc_sha256) is None
                or not cdi_file
                or re.fullmatch(r"[0-9a-f]{64}", cdi_sha256) is None
            ):
                raise ValueError("Live2D 绑定配件的模型锁定或 Part 声明无效")
            moc_path = (source_root / str(references.get("Moc", ""))).resolve()
            if _sha256(moc_path).lower() != model_moc_sha256:
                raise ValueError("Live2D 绑定配件与当前 MOC3 哈希不一致")
            cdi_path = (source_root / cdi_file).resolve()
            if source_root not in cdi_path.parents or not cdi_path.is_file():
                raise FileNotFoundError(
                    f"Live2D 绑定配件 CDI 缺失或越界：{cdi_file}"
                )
            if _sha256(cdi_path).lower() != cdi_sha256:
                raise ValueError("Live2D 绑定配件与当前 CDI 哈希不一致")
            cdi = json.loads(cdi_path.read_text(encoding="utf-8-sig"))
            cdi_part_ids = {
                str(part.get("Id", "")).strip()
                for part in cdi.get("Parts", [])
                if isinstance(part, dict)
            }
            missing_part_ids = sorted(
                {head_part_id, nose_part_id} - cdi_part_ids
            )
            if missing_part_ids:
                raise ValueError(
                    "Live2D 绑定配件引用了 CDI 未登记的 Part："
                    + ", ".join(missing_part_ids)
                )
            checked.append(cdi_path)
        roles: set[str] = set()
        role_pin_targets: dict[str, str] = {}
        for index, layer in enumerate(accessory_layers):
            if not isinstance(layer, dict):
                raise ValueError(
                    f"Live2D 常驻配件 layers[{index}] 必须是对象"
                )
            role = str(layer.get("role", "")).strip()
            file_value = str(layer.get("file", "")).strip()
            position_ratio = layer.get("position_ratio")
            width_ratio = layer.get("width_ratio")
            rotation_degrees = layer.get("rotation_degrees")
            pin_to = str(layer.get("pin_to", "")).strip()
            position_valid = (
                isinstance(position_ratio, list)
                and len(position_ratio) == 2
                and all(
                    not isinstance(item, bool)
                    and isinstance(item, (int, float))
                    and math.isfinite(float(item))
                    for item in position_ratio
                )
            )
            width_valid = (
                not isinstance(width_ratio, bool)
                and isinstance(width_ratio, (int, float))
                and math.isfinite(float(width_ratio))
                and float(width_ratio) > 0
            )
            rotation_valid = (
                not isinstance(rotation_degrees, bool)
                and isinstance(rotation_degrees, (int, float))
                and math.isfinite(float(rotation_degrees))
            )
            if (
                not role
                or role in roles
                or not file_value
                or not position_valid
                or not width_valid
                or not rotation_valid
                or (accessory_schema == 2 and pin_to not in {"head", "nose"})
            ):
                raise ValueError(
                    f"Live2D 常驻配件 layers[{index}] 无效"
                )
            roles.add(role)
            role_pin_targets[role] = pin_to
            accessory_path = (
                accessory_manifest_path.parent / file_value
            ).resolve()
            if (
                source_root not in accessory_path.parents
                or not accessory_path.is_file()
            ):
                raise FileNotFoundError(
                    f"Live2D 常驻配件图片缺失或越界：{file_value}"
                )
            with Image.open(accessory_path) as image:
                if image.mode != "RGBA" or image.width <= 0 or image.height <= 0:
                    raise ValueError(
                        "Live2D 常驻配件图片必须是有效 RGBA PNG："
                        f"{file_value}"
                    )
                if image.format != "PNG":
                    raise ValueError(
                        f"Live2D 常驻配件图片必须是 PNG：{file_value}"
                    )
            checked.append(accessory_path)
        if accessory_ear_pair_center == "nose" and (
            {"pig_ear_left", "pig_ear_right"} - roles
            or role_pin_targets.get("pig_ear_left") != "head"
            or role_pin_targets.get("pig_ear_right") != "head"
        ):
            raise ValueError(
                "Live2D 双耳鼻中轴约束需要一对 pin_to=head 的猪耳"
            )
        accessory_roles = sorted(roles)
        accessory_manifest_relative = (
            "live2d/"
            + accessory_manifest_path.relative_to(source_root).as_posix()
        )

    destination = runtime_stage / "live2d"
    destination.mkdir(parents=True)
    for source in dict.fromkeys(checked):
        relative = source.relative_to(source_root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    staged_model_path = destination / model_path.relative_to(source_root)
    staged_model = json.loads(staged_model_path.read_text(encoding="utf-8-sig"))
    compatibility_defaults_added: list[str] = []
    for key in ("Groups", "HitAreas"):
        if key not in staged_model:
            staged_model[key] = []
            compatibility_defaults_added.append(key)
    staged_model_path.write_text(
        json.dumps(staged_model, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    blink_value = str(raw.get("blink_overlay_file", "")).strip()
    blink_relative: str | None = None
    if blink_value:
        blink_source = (workspace / blink_value).resolve()
        if (
            workspace_root not in blink_source.parents
            or not blink_source.is_file()
        ):
            raise FileNotFoundError(f"Live2D 眨眼叠层无效：{blink_source}")
        blink_destination = destination / "blink-overlay.png"
        shutil.copy2(blink_source, blink_destination)
        checked.append(blink_source)
        blink_relative = "live2d/blink-overlay.png"

    manifest_config = {
        "enabled": True,
        "idle_backend": "live2d",
        "model": f"live2d/{model_name}",
        "blink_overlay": blink_relative,
        "layer_manifest": layer_manifest_relative,
        "accessory_manifest": accessory_manifest_relative,
        "load_timeout_ms": load_timeout_ms,
        "idle_animation": normalize_live2d_idle_animation(
            raw.get("idle_animation")
        ),
        "persistent_parameters": normalize_live2d_persistent_parameters(
            raw.get("persistent_parameters")
        ),
        "live2d_state_map": live2d_state_map,
        "sprite_states": list(raw.get("sprite_states", [])),
        "fallback_backend": str(raw.get("fallback_backend", "sprite")),
    }
    qa = {
        "status": "passed",
        "model": model_name,
        "file_count": len(checked),
        "files": [
            {
                "path": path.relative_to(workspace_root).as_posix(),
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
            for path in checked
        ],
        "fallback_backend": manifest_config["fallback_backend"],
        "render_backend": (
            "layered-webgl"
            if layer_manifest_relative
            else "cubism-parameters"
        ),
        "layered_roles": layered_roles,
        "accessory_roles": accessory_roles,
        "accessory_binding_mode": accessory_binding_mode,
        "accessory_render_layer": accessory_render_layer,
        "compatibility_defaults_added": compatibility_defaults_added,
        "staged_model_sha256": _sha256(staged_model_path),
    }
    return manifest_config, qa


def _assemble_workspace_impl(
    workspace: Path,
    build_id: str,
) -> tuple[Path, dict[str, Any]]:
    """在 staging 中按声明式配方生成、验证并事务式提交运行资源。"""

    validation = validate_workspace(workspace)
    if validation["status"] != "passed":
        details = "\n".join(f"- {item}" for item in validation["failures"])
        raise ValueError(f"工作区素材未通过检查：\n{details}")
    workflow = workspace / "workflow.json"
    require_custom_pet_approved(workflow)
    workflow_state = load_workflow(workflow)
    binding_failures = validate_workflow_bindings(workspace)
    if binding_failures:
        raise ValueError(
            "审批与当前素材不一致：\n"
            + "\n".join(f"- {failure}" for failure in binding_failures)
        )
    touch_failures = validate_required_touch_transitions(workspace)
    if touch_failures:
        raise ValueError(
            "摸头过渡帧派生证据无效：\n"
            + "\n".join(f"- {failure}" for failure in touch_failures)
        )
    build_input_evidence = workspace_build_input_evidence(workspace)

    config = load_config(workspace)
    recipe_path = PROJECT_ROOT / str(config["animation_recipe"])
    recipe = load_recipe(recipe_path)
    canvas_size = tuple(int(value) for value in config["canvas_size"])
    role_heights = {
        "standing": int(config["standing_height"]),
        "seated": int(config["seated_height"]),
        "sleeping": int(config["sleeping_height"]),
    }
    source_cleanup = dict(config.get("source_cleanup", {}))
    cleanup_enabled = bool(source_cleanup.get("green_edge_despill", False))
    cleanup_radius = int(source_cleanup.get("edge_radius", 4))
    cleanup_stats: dict[str, dict[str, int]] = {}

    def prepare_source(name: str, image: Image.Image) -> Image.Image:
        """按显式配置清理派生帧输入，并记录清理前后绿像素数。"""

        before = count_green_pixels(image)
        prepared = (
            despill_green_edges(image, edge_radius=cleanup_radius)
            if cleanup_enabled
            else image
        )
        cleanup_stats[name] = {
            "green_pixels_before": before,
            "green_pixels_after": count_green_pixels(prepared),
        }
        return prepared

    action_root = workspace / str(config["actions_directory"])
    walk_root = workspace / str(config["walk_directory"])
    runtime_root = workspace / str(config["runtime_directory"])
    review_root = workspace / str(config["review_directory"])
    source_poses = {
        "stand": prepare_source(
            "stand",
            load_rgba(workspace / str(config["character_file"])),
        ),
        **{
            name: prepare_source(
                name,
                load_rgba(action_root / f"{name}.png"),
            )
            for name in ACTION_NAMES
        },
    }
    walk_files = [
        walk_root / f"walk_{index:02d}.png" for index in range(1, 9)
    ]
    interaction_fallback = interaction_fallback_is_allowed(
        config,
        workflow_state,
    )
    raw_state_fallbacks = config.get("state_fallbacks", {})
    if not isinstance(raw_state_fallbacks, dict):
        raise ValueError("state_fallbacks 必须是 JSON 对象")
    state_fallbacks: dict[str, str] = {}
    for raw_state, raw_fallback in raw_state_fallbacks.items():
        state = str(raw_state).strip()
        fallback = str(raw_fallback).strip()
        if state == fallback:
            continue
        if state not in recipe["animations"] or fallback not in recipe["animations"]:
            raise ValueError(
                f"未知基础状态回退：{state!r} -> {fallback!r}"
            )
        if (
            int(recipe["animations"][state]["count"]) != 1
            or int(recipe["animations"][fallback]["count"]) != 1
        ):
            raise ValueError("基础状态回退只允许用于单帧表情状态")
        if fallback in raw_state_fallbacks:
            raise ValueError("state_fallbacks 不允许链式或循环回退")
        state_fallbacks[state] = fallback
        state_source = str(
            recipe["animations"][state]["frames"][0]["source"]
        )
        fallback_source = str(
            recipe["animations"][fallback]["frames"][0]["source"]
        )
        if state_source in source_poses and fallback_source in source_poses:
            source_poses[state_source] = source_poses[fallback_source]
    if interaction_fallback:
        stationary_frame = normalize_pose(
            source_poses["stand"],
            role_heights["standing"],
            canvas_size=canvas_size,
        )
        walk_frames = [stationary_frame.copy() for _index in range(8)]
    else:
        walk_frames = [load_rgba(path) for path in walk_files]
    if any(frame.size != canvas_size for frame in walk_frames):
        raise ValueError(f"走路帧必须已经规范为运行画布：{canvas_size}")

    visual_mass = dict(config["visual_mass"])
    visual_mass_mode = str(visual_mass.get("mode", "walk_median_safe"))
    walk_anchor_mass = median(alpha_mass(frame) for frame in walk_frames)
    target_visual_mass: float | None
    if visual_mass_mode == "height":
        target_visual_mass = None
        walk_shared_scale = 1.0
        runtime_walk_frames = walk_frames
    elif visual_mass_mode == "walk_median_safe":
        static_names = (
            "stand",
            "wave",
            "happy",
            "selfie",
            "surprised",
            "annoyed",
            "drag",
        )
        pose_limits = [
            maximum_fitted_alpha_mass(
                source_poses[name],
                canvas_size=canvas_size,
                bottom_offset=18 if name == "drag" else 3 if name == "selfie" else 0,
                horizontal_offset=3 if name == "drag" else 1 if name == "selfie" else 0,
            )
            for name in static_names
        ]
        target_visual_mass = min(walk_anchor_mass, *pose_limits)
        minimum_ratio = float(visual_mass.get("minimum_target_ratio", 0.6))
        if target_visual_mass < walk_anchor_mass * minimum_ratio:
            raise ValueError(
                "安全共同 alpha mass 过低："
                f"{target_visual_mass:.1f} < 走路中位数 {walk_anchor_mass:.1f} "
                f"的 {minimum_ratio:.0%}；请检查异常宽/高动作源图。"
            )
        walk_shared_scale = (target_visual_mass / walk_anchor_mass) ** 0.5
        runtime_walk_frames = [
            scaled_frame(frame, walk_shared_scale, canvas_size=canvas_size)
            for frame in walk_frames
        ]
    else:
        raise ValueError(f"未知 visual_mass.mode：{visual_mass_mode}")

    build_stage = workspace / "staging" / build_id
    runtime_stage = build_stage / "runtime"
    review_stage = build_stage / "review"
    runtime_stage.mkdir(parents=True)
    review_stage.mkdir(parents=True)
    expected_masses: dict[str, float] = {}

    def render_spec(spec: dict[str, Any]) -> tuple[Image.Image, float | None]:
        source = source_poses[str(spec["source"])]
        role = str(spec.get("role", "standing"))
        height = round(
            role_heights[role] * float(spec.get("height_factor", 1.0))
        )
        x = int(spec.get("x", 0))
        y = int(spec.get("y", 0))
        factor = float(spec.get("mass_factor", 1.0))
        if target_visual_mass is not None:
            expected = target_visual_mass * factor
            frame = normalize_pose_by_alpha_mass(
                source,
                expected,
                canvas_size=canvas_size,
                bottom_offset=y,
                horizontal_offset=x,
            )
            return frame, expected
        return (
            normalize_pose(
                source,
                height,
                canvas_size=canvas_size,
                bottom_offset=y,
                horizontal_offset=x,
            ),
            None,
        )

    output_directories = {
        "idle": "idle",
        "walk": "walk",
        "sit": "sit",
        "sleep": "sleep",
        "drag": "interact",
        "selfie": "interact",
        "wave": "interact",
    }
    animations: dict[str, list[str]] = {}
    rendered_by_state: dict[str, list[Image.Image]] = {}
    optional_transition_paths: list[str] = []
    try:
        for state in recipe["state_order"]:
            fallback = state_fallbacks.get(state)
            if fallback is not None:
                if fallback not in rendered_by_state:
                    raise ValueError(
                        f"基础状态回退目标必须先于原状态构建：{state} -> {fallback}"
                    )
                rendered_by_state[state] = rendered_by_state[fallback]
                animations[state] = list(animations[fallback])
                continue
            entry = recipe["animations"][state]
            if entry["kind"] == "walk_input":
                frames = runtime_walk_frames
                expected = (
                    [
                        alpha_mass(source) * walk_shared_scale * walk_shared_scale
                        for source in walk_frames
                    ]
                    if target_visual_mass is not None
                    else [None] * len(frames)
                )
            else:
                rendered = [render_spec(spec) for spec in entry["frames"]]
                frames = [item[0] for item in rendered]
                expected = [item[1] for item in rendered]
            rendered_by_state[state] = frames
            directory = output_directories.get(state, "expressions")
            paths = save_frames(runtime_stage, directory, state, frames)
            animations[state] = paths
            for relative, mass in zip(paths, expected, strict=True):
                if mass is not None:
                    expected_masses[relative] = mass

        touch_sources = recipe["touch_feedback"]["optional_transition_sources"]
        touch_frames: list[Image.Image] = []
        for source_relative in touch_sources:
            source_path = workspace / source_relative
            if source_path.is_file():
                source_image = prepare_source(
                    source_relative,
                    load_rgba(source_path),
                )
                expected: float | None = None
                if target_visual_mass is not None:
                    frame = normalize_pose_by_alpha_mass(
                        source_image,
                        target_visual_mass,
                        canvas_size=canvas_size,
                    )
                    expected = target_visual_mass
                else:
                    frame = normalize_pose(
                        source_image,
                        role_heights["standing"],
                        canvas_size=canvas_size,
                    )
                touch_frames.append(frame)
                relative = (
                    f"interact/touch_transition_{len(touch_frames):02d}.png"
                )
                destination = runtime_stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                frame.save(destination, "PNG", optimize=True)
                optional_transition_paths.append(relative)
                if expected is not None:
                    expected_masses[relative] = expected
        maximum_optional = int(
            recipe["touch_feedback"]["maximum_optional_frames"]
        )
        if len(touch_frames) > maximum_optional:
            raise ValueError("摸头过渡帧超过声明式配方允许的 2 张")
        required_touch_frames = int(
            config.get("touch_feedback", {}).get(
                "default_transition_frame_count",
                0,
            )
        )
        if required_touch_frames not in (0, 1, 2):
            raise ValueError("default_transition_frame_count 只能是 0、1 或 2")
        if len(touch_frames) < required_touch_frames:
            missing = required_touch_frames - len(touch_frames)
            raise ValueError(
                "新宠物默认摸头方案要求 "
                f"{required_touch_frames} 张过渡帧，当前缺少 {missing} 张；"
                "旧宠物如需兼容回退，可从迁移配置中省略 "
                "default_transition_frame_count"
            )

        stand = rendered_by_state["idle"][0]
        icon_source = stand.crop(alpha_bbox(stand))
        icon_source.thumbnail((118, 118), Image.Resampling.LANCZOS)
        icon = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        icon.alpha_composite(
            icon_source,
            ((128 - icon_source.width) // 2, (128 - icon_source.height) // 2),
        )
        icon.save(runtime_stage / "icon.png", "PNG", optimize=True)
        hybrid_manifest, hybrid_qa = stage_hybrid_runtime(
            workspace,
            config,
            runtime_stage,
        )
        extension_manifest, extension_paths, extension_qa = (
            stage_extension_animations(
                workspace,
                config,
                runtime_stage,
                canvas_size=canvas_size,
                base_state_heights={
                    state: round(
                        median(
                            alpha_bbox(frame)[3] - alpha_bbox(frame)[1]
                            for frame in frames
                        )
                    )
                    for state, frames in rendered_by_state.items()
                },
            )
        )
        directional_manifest, directional_paths, directional_qa = (
            stage_directional_mouse_follow(
                workspace,
                config,
                runtime_stage,
                canvas_size=canvas_size,
            )
        )

        touch_config = {
            **recipe["touch_feedback"],
            **config.get("touch_feedback", {}),
            "transition_frames": optional_transition_paths,
        }
        break_reminders = canonical_break_reminder_manifest(
            config.get("break_reminders", {})
        )
        workspace_root = workspace.resolve()
        runtime_stage_root = runtime_stage.resolve()
        for relative in break_reminders["sounds"]["files"].values():
            source = (workspace_root / relative).resolve()
            destination = (runtime_stage_root / relative).resolve()
            if (
                not source.is_relative_to(workspace_root)
                or not destination.is_relative_to(runtime_stage_root)
                or not source.is_file()
            ):
                raise ValueError(f"休息提醒音效不存在或路径越界：{relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        runtime_asset_fingerprint = runtime_asset_bundle_sha256(runtime_stage)
        manifest: dict[str, Any] = {
            "schema_version": 2,
            "pet_id": config["pet_id"],
            "build_id": build_id,
            "build_input_schema_version": build_input_evidence["schema_version"],
            "build_input_fingerprint": build_input_evidence["fingerprint"],
            "application_fingerprint": build_input_evidence[
                "application_fingerprint"
            ],
            "runtime_asset_fingerprint": runtime_asset_fingerprint,
            "display_name": config["display_name"],
            "pet_mode": config.get("pet_mode", "full-body"),
            "stationary": interaction_fallback,
            "motion_source": (
                "approved_interaction_fallback"
                if interaction_fallback
                else "eight_phase_walk"
            ),
            "rendering_mode": str(config.get("rendering_mode", "sprite")),
            "recommended_display_height": config["recommended_display_height"],
            "canvas_size": list(canvas_size),
            "base_frame_count": recipe["base_frame_count"],
            "optional_touch_frame_count": len(optional_transition_paths),
            "extension_frame_count": sum(
                len(value["frames"]) for value in extension_manifest.values()
            ),
            "directional_frame_count": (
                len(directional_manifest["frames"])
                if directional_manifest is not None
                else 0
            ),
            "total_frame_count": recipe["base_frame_count"]
            + len(optional_transition_paths),
            "total_asset_frame_count": (
                recipe["base_frame_count"]
                + len(optional_transition_paths)
                + sum(
                    len(value["frames"])
                    for value in extension_manifest.values()
                )
                + (
                    len(directional_manifest["frames"])
                    if directional_manifest is not None
                    else 0
                )
            ),
            "animation_recipe": str(config["animation_recipe"]),
            "target_standing_height": role_heights["standing"],
            "visual_mass_normalization": visual_mass_mode,
            "target_visual_alpha_mass": (
                round(target_visual_mass, 2)
                if target_visual_mass is not None
                else None
            ),
            "walk_shared_scale_factor": round(walk_shared_scale, 6),
            "walk_phases": recipe["animations"]["walk"]["phases"],
            "walk_motion_factors": recipe["animations"]["walk"]["motion_factors"],
            "front_walk_mirror_with_direction": bool(
                config.get("front_walk_mirror_with_direction", True)
            ),
            "front_walk_mirror_direction": str(
                config.get("front_walk_mirror_direction", "left")
            ).strip().lower(),
            "animations": animations,
            "state_fallbacks": state_fallbacks,
            "touch_feedback": touch_config,
            "companion_behaviors": dict(
                config.get("companion_behaviors", {})
            ),
            "companion_ui": normalize_companion_ui_config(
                config.get("companion_ui")
            ),
            "dialogue_system": normalize_dialogue_config(
                config.get("dialogue_system", {})
            ),
            "care_simulation": normalize_care_config(
                config.get("care_simulation", {})
            ),
            "break_reminders": break_reminders,
            "extension_animations": extension_manifest,
            "icon": "icon.png",
            "selfie_photo": (
                "../selfie.png"
                if (
                    workspace / str(config["selfie_file"])
                ).is_file()
                else None
            ),
            "wake_behavior": "reverse_sleep_then_reverse_sit",
        }
        if hybrid_manifest is not None:
            manifest["hybrid_runtime"] = hybrid_manifest
        if directional_manifest is not None:
            manifest["directional_mouse_follow"] = directional_manifest
        (runtime_stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        action_previews = {
            "stand": stand,
            "wave": rendered_by_state["wave"][0],
            "happy": rendered_by_state["happy"][0],
            "selfie": rendered_by_state["selfie"][0],
            "surprised": rendered_by_state["surprised"][0],
            "annoyed": rendered_by_state["annoyed"][0],
            "sit": rendered_by_state["sit"][-1],
            "sleep": rendered_by_state["sleep"][-1],
            "drag": rendered_by_state["drag"][1],
        }
        for state in state_fallbacks:
            action_previews.pop(state, None)
        make_action_sheet(
            action_previews,
            review_stage / "all-actions-contact-sheet.jpg",
        )
        make_action_sheet(
            action_previews,
            review_stage / "all-actions-contact-sheet.png",
        )
        transparent_root = review_stage / "transparent-actions"
        transparent_root.mkdir(parents=True)
        for name, image in action_previews.items():
            image.save(transparent_root / f"{name}.png", "PNG", optimize=True)
        make_animation_preview(
            runtime_walk_frames,
            review_stage / "walk-runtime-preview.gif",
            canvas_size=canvas_size,
            duration=90,
        )
        sit_frames = rendered_by_state["sit"]
        sleep_frames = rendered_by_state["sleep"]
        wake_frames = list(reversed(sleep_frames[:-1])) + list(
            reversed(sit_frames[:-1])
        )
        make_animation_preview(
            sit_frames + sleep_frames + wake_frames,
            review_stage / "sit-sleep-wake-preview.gif",
            canvas_size=canvas_size,
            duration=180,
        )
        touch_preview_frames = [
            rendered_by_state["surprised"][0],
            rendered_by_state["shy"][0],
        ]
        if touch_frames:
            touch_preview_frames.extend(touch_frames)
        else:
            touch_preview_frames.append(
                Image.blend(
                    rendered_by_state["shy"][0],
                    rendered_by_state["idle"][0],
                    0.55,
                )
            )
        touch_preview_frames.append(rendered_by_state["idle"][0])
        touch_preview_durations = [320, 760]
        touch_preview_durations.extend([180] * (len(touch_preview_frames) - 3))
        touch_preview_durations.append(160)
        make_animation_preview(
            touch_preview_frames,
            review_stage / "touch-feedback-preview.gif",
            canvas_size=canvas_size,
            duration=touch_preview_durations,
        )
        review_visual_fingerprint = review_visual_evidence_sha256(review_stage)
        walk_report = (
            {
                "status": "passed",
                "failures": [],
                "mode": "approved_interaction_static_fallback",
                "frame_count": 8,
            }
            if interaction_fallback
            else build_walk_quality_report(
                runtime_walk_frames,
                dict(config["walk_qa"]),
            )
        )
        report = build_quality_report(
            manifest,
            runtime_stage,
            canvas_size=canvas_size,
            expected_masses=expected_masses,
            visual_mass=visual_mass,
            extra_frames=optional_transition_paths,
            additional_frames=extension_paths + directional_paths,
            walk_report=walk_report,
        )
        report.update(
            {
                "base_frame_count": recipe["base_frame_count"],
                "optional_touch_frame_count": len(optional_transition_paths),
                "total_frame_count": recipe["base_frame_count"]
                + len(optional_transition_paths),
                "extension_frame_count": manifest["extension_frame_count"],
                "directional_frame_count": manifest["directional_frame_count"],
                "total_asset_frame_count": manifest["total_asset_frame_count"],
                "recipe": str(config["animation_recipe"]),
                "build_id": build_id,
                "build_input_schema_version": build_input_evidence[
                    "schema_version"
                ],
                "build_input_fingerprint": build_input_evidence["fingerprint"],
                "application_fingerprint": build_input_evidence[
                    "application_fingerprint"
                ],
                "runtime_asset_fingerprint": runtime_asset_fingerprint,
                "review_visual_fingerprint": review_visual_fingerprint,
                "hybrid_runtime_qa": hybrid_qa,
                "extension_animations_qa": extension_qa,
                "directional_mouse_follow_qa": directional_qa,
                "source_cleanup": {
                    "green_edge_despill": cleanup_enabled,
                    "edge_radius": cleanup_radius if cleanup_enabled else None,
                    "sources": cleanup_stats,
                },
                "touch_feedback_qa": {
                    "status": "passed",
                    "sequence": [
                        "surprised",
                        "shy-or-happy",
                        "transition",
                        "idle",
                    ],
                    "durations_ms": touch_config["durations_ms"],
                    "transition_frame_count": len(optional_transition_paths),
                    "fallback": (
                        None
                        if optional_transition_paths
                        else "in-memory reaction-to-idle blend"
                    ),
                    "preview": "touch-feedback-preview.gif",
                },
            }
        )
        (review_stage / "assets-qa.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if report["status"] != "passed":
            details = "\n".join(f"- {item}" for item in report["failures"])
            raise ValueError(f"运行素材自动检查未通过：\n{details}")
    except Exception as exc:
        failed_root = workspace / "failed-builds"
        failed_root.mkdir(parents=True, exist_ok=True)
        failed = failed_root / build_id
        if build_stage.exists():
            try:
                build_stage.rename(failed)
            except OSError as archive_error:
                # Windows 的图片解码器、索引器或杀毒软件可能短暂持有 staging
                # 句柄。失败归档只是诊断辅助，不能覆盖真正的构建异常。
                marker = failed_root / f"{build_id}.archive-error.txt"
                marker.write_text(
                    "staging 归档失败，原始构建异常仍会继续抛出。\n"
                    f"staging: {build_stage}\n"
                    f"error: {archive_error}\n",
                    encoding="utf-8",
                )
        review_root.mkdir(parents=True, exist_ok=True)
        failure_report = {
            "schema_version": 1,
            "pet_id": config["pet_id"],
            "build_id": build_id,
            "build_input_schema_version": build_input_evidence["schema_version"],
            "build_input_fingerprint": build_input_evidence["fingerprint"],
            "application_fingerprint": build_input_evidence[
                "application_fingerprint"
            ],
            "failed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "failed_build": (
                failed.relative_to(workspace).as_posix()
                if failed.exists()
                else build_stage.relative_to(workspace).as_posix()
            ),
        }
        (review_root / "latest-failure.json").write_text(
            json.dumps(failure_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise

    if runtime_root.exists():
        backup_directory(runtime_root, workspace / "backups", "runtime")
    runtime_stage.rename(runtime_root)
    review_build = review_root / "builds" / build_id
    review_build.parent.mkdir(parents=True, exist_ok=True)
    review_stage.rename(review_build)
    for name in (
        "all-actions-contact-sheet.jpg",
        "all-actions-contact-sheet.png",
        "walk-runtime-preview.gif",
        "sit-sleep-wake-preview.gif",
        "touch-feedback-preview.gif",
        "assets-qa.json",
    ):
        source = review_build / name
        temporary = review_root / f".{name}.tmp"
        shutil.copy2(source, temporary)
        temporary.replace(review_root / name)
    (review_root / "latest-build.json").write_text(
        json.dumps(
            {
                "pet_id": config["pet_id"],
                "build_id": build_id,
                "path": review_build.relative_to(workspace).as_posix(),
                "build_input_schema_version": build_input_evidence[
                    "schema_version"
                ],
                "build_input_fingerprint": build_input_evidence["fingerprint"],
                "application_fingerprint": build_input_evidence[
                    "application_fingerprint"
                ],
                "runtime_asset_fingerprint": runtime_asset_fingerprint,
                "review_visual_fingerprint": review_visual_fingerprint,
                "runtime_manifest_sha256": _sha256(runtime_root / "manifest.json"),
                "assets_qa_sha256": _sha256(review_build / "assets-qa.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    latest_failure = review_root / "latest-failure.json"
    if latest_failure.is_file():
        latest_failure.unlink()
    if build_stage.exists():
        build_stage.rmdir()
    return runtime_root, report


def assemble_workspace(workspace: Path) -> tuple[Path, dict[str, Any]]:
    """统一记录所有构建失败，包括 staging 建立前的配方与输入异常。"""

    build_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        return _assemble_workspace_impl(workspace, build_id)
    except Exception as exc:
        try:
            config = load_config(workspace)
            review_root = workspace / str(config.get("review_directory", "review"))
            pet_id = str(config.get("pet_id", workspace.name))
        except Exception:
            review_root = workspace / "review"
            pet_id = workspace.name
        review_root.mkdir(parents=True, exist_ok=True)
        latest_failure = review_root / "latest-failure.json"
        existing: dict[str, Any] = {}
        if latest_failure.is_file():
            try:
                raw = json.loads(latest_failure.read_text(encoding="utf-8-sig"))
                if isinstance(raw, dict):
                    existing = raw
            except (OSError, UnicodeError, json.JSONDecodeError):
                existing = {}
        if existing.get("build_id") != build_id:
            try:
                evidence = workspace_build_input_evidence(workspace)
            except Exception:
                evidence = {}
            failed_path = workspace / "failed-builds" / build_id
            if not failed_path.exists():
                failed_path = workspace / "staging" / build_id
            failure_report = {
                "schema_version": 1,
                "pet_id": pet_id,
                "build_id": build_id,
                "build_input_schema_version": evidence.get("schema_version"),
                "build_input_fingerprint": evidence.get("fingerprint"),
                "application_fingerprint": evidence.get(
                    "application_fingerprint"
                ),
                "failed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "failed_build": (
                    failed_path.relative_to(workspace).as_posix()
                    if failed_path.exists()
                    else None
                ),
            }
            latest_failure.write_text(
                json.dumps(failure_report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        raise


def activate_workspace(workspace: Path, runtime_root: Path) -> dict[str, str | None]:
    """把已验证运行目录事务式激活为当前私有桌宠。"""

    config = load_config(workspace)
    require_custom_pet_approved(workspace / "workflow.json")
    binding_failures = validate_workflow_bindings(workspace)
    if binding_failures:
        raise ValueError(
            "当前工作流来源或审批绑定已失效，拒绝激活：\n"
            + "\n".join(f"- {item}" for item in binding_failures)
        )
    expected_runtime_root = (
        workspace / str(config["runtime_directory"])
    ).resolve()
    if runtime_root.resolve() != expected_runtime_root:
        raise ValueError("只允许激活当前工作区配置声明的 runtime 目录")
    attestation = inspect_runtime_build_attestation(workspace)
    if attestation.get("status") != "passed":
        details = "\n".join(
            f"- {item}" for item in attestation.get("failures", [])
        )
        raise ValueError(
            "当前 runtime 构建取证未通过，拒绝激活"
            + (f"：\n{details}" if details else "")
        )
    qa_path = workspace / str(config["review_directory"]) / "assets-qa.json"
    if not qa_path.is_file():
        raise FileNotFoundError("缺少最新 assets-qa.json，拒绝激活")
    qa = json.loads(qa_path.read_text(encoding="utf-8-sig"))
    if qa.get("status") != "passed":
        raise ValueError("最新素材 QA 未通过，拒绝激活")
    selfie = workspace / str(config["selfie_file"])
    selfie_required = bool(config.get("selfie_required", True))
    if selfie_required and not selfie.is_file():
        raise FileNotFoundError(f"缺少必须的原始自拍：{selfie}")
    active_parent = ACTIVE_PET_ROOT.resolve().parent
    if active_parent != USER_ROOT.resolve():
        raise RuntimeError("拒绝替换 user_assets 之外的目录")
    activation_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    activation_stage = USER_ROOT / ".staging" / f"activate-{activation_id}"
    staged_pet = activation_stage / "pet"
    staged_selfie = activation_stage / "selfie.png"
    activation_stage.mkdir(parents=True)
    shutil.copytree(runtime_root, staged_pet)
    manifest = json.loads(
        (staged_pet / "manifest.json").read_text(encoding="utf-8-sig")
    )
    if manifest.get("pet_id") != config["pet_id"]:
        raise ValueError("staging manifest 的 pet_id 与工作区不一致")
    if int(manifest.get("total_frame_count", 38)) not in (38, 39, 40):
        raise ValueError("staging 运行帧必须为 38 帧基础 + 0–2 摸头过渡帧")
    if selfie.is_file():
        shutil.copy2(selfie, staged_selfie)

    selfie_destination = USER_ROOT / "selfie.png"
    selfie_backup: Path | None = None
    if selfie_destination.is_file():
        selfie_backup = (
            USER_ROOT
            / "backups"
            / f"selfie-{timestamp()}{selfie_destination.suffix}"
        )
        selfie_backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selfie_destination, selfie_backup)
    active_backup = backup_directory(
        ACTIVE_PET_ROOT,
        USER_ROOT / "backups",
        "pet",
    )
    try:
        staged_pet.rename(ACTIVE_PET_ROOT)
        if staged_selfie.is_file():
            staged_selfie.replace(selfie_destination)
        elif selfie_destination.is_file():
            selfie_destination.unlink()
    except Exception:
        if ACTIVE_PET_ROOT.exists():
            failed = USER_ROOT / "failed-activations" / activation_id
            failed.parent.mkdir(parents=True, exist_ok=True)
            ACTIVE_PET_ROOT.rename(failed)
        if active_backup and active_backup.exists():
            active_backup.rename(ACTIVE_PET_ROOT)
        if selfie_backup and selfie_backup.exists():
            shutil.copy2(selfie_backup, selfie_destination)
        raise
    active_state = {
        "version": 2,
        "pet_id": config["pet_id"],
        "display_name": config["display_name"],
        "workspace": workspace.relative_to(PROJECT_ROOT).as_posix(),
        "activated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "workflow": (
            workspace / "workflow.json"
        ).relative_to(PROJECT_ROOT).as_posix(),
        "rollback": {
            "pet": str(active_backup) if active_backup else None,
            "selfie": str(selfie_backup) if selfie_backup else None,
        },
    }
    pointer = USER_ROOT / "active-pet.json"
    pointer_tmp = USER_ROOT / ".active-pet.json.tmp"
    pointer_tmp.write_text(
        json.dumps(active_state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    pointer_tmp.replace(pointer)
    if activation_stage.exists():
        activation_stage.rmdir()
    return {
        "previous_pet_backup": str(active_backup) if active_backup else None,
        "previous_selfie_backup": str(selfie_backup) if selfie_backup else None,
    }


def rollback_active_pet() -> Path:
    """按 active-pet.json 记录恢复最近可用运行目录。"""

    pointer = USER_ROOT / "active-pet.json"
    if not pointer.is_file():
        raise FileNotFoundError("没有 active-pet.json，无法回滚")
    state = json.loads(pointer.read_text(encoding="utf-8-sig"))
    rollback = state.get("rollback", {})
    pet_backup_value = rollback.get("pet") if isinstance(rollback, dict) else None
    if not pet_backup_value:
        raise ValueError("活动指针没有可用的上一版本备份")
    pet_backup = Path(str(pet_backup_value))
    if not pet_backup.is_absolute():
        pet_backup = PROJECT_ROOT / pet_backup
    if not pet_backup.is_dir():
        raise FileNotFoundError(f"回滚目录不存在：{pet_backup}")
    current_backup = backup_directory(
        ACTIVE_PET_ROOT,
        USER_ROOT / "backups",
        "rollback-current",
    )
    selfie_destination = USER_ROOT / "selfie.png"
    current_selfie_backup: Path | None = None
    if selfie_destination.is_file():
        current_selfie_backup = (
            USER_ROOT / "backups" / f"rollback-selfie-{timestamp()}.png"
        )
        current_selfie_backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selfie_destination, current_selfie_backup)
    previous_selfie_value = (
        rollback.get("selfie") if isinstance(rollback, dict) else None
    )
    previous_selfie = (
        Path(str(previous_selfie_value)) if previous_selfie_value else None
    )
    if previous_selfie is not None and not previous_selfie.is_absolute():
        previous_selfie = PROJECT_ROOT / previous_selfie
    try:
        pet_backup.rename(ACTIVE_PET_ROOT)
        if previous_selfie is not None and previous_selfie.is_file():
            shutil.copy2(previous_selfie, selfie_destination)
        elif selfie_destination.is_file():
            selfie_destination.unlink()
    except Exception:
        if current_backup and current_backup.exists():
            current_backup.rename(ACTIVE_PET_ROOT)
        if current_selfie_backup and current_selfie_backup.is_file():
            shutil.copy2(current_selfie_backup, selfie_destination)
        raise
    manifest = json.loads(
        (ACTIVE_PET_ROOT / "manifest.json").read_text(encoding="utf-8-sig")
    )
    state.update(
        {
            "pet_id": manifest.get("pet_id", state.get("pet_id")),
            "display_name": manifest.get(
                "display_name",
                state.get("display_name"),
            ),
            "rolled_back_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "rollback": {
                "pet": str(current_backup) if current_backup else None,
                "selfie": (
                    str(current_selfie_backup)
                    if current_selfie_backup
                    else None
                ),
            },
        }
    )
    temporary = USER_ROOT / ".active-pet.json.tmp"
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(pointer)
    return ACTIVE_PET_ROOT


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="构建独立桌宠工作区")
    parser.add_argument("pet_id")
    parser.add_argument(
        "--activate",
        action="store_true",
        help="通过两次门禁后替换当前 user_assets/pet",
    )
    parser.add_argument(
        "--activate-existing",
        action="store_true",
        help="激活已通过 QA 的 runtime，不重新装配",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="恢复 active-pet.json 记录的上一运行版本",
    )
    return parser.parse_args()


def main() -> int:
    """构建并按需激活桌宠。"""

    args = parse_args()
    if args.rollback:
        restored = rollback_active_pet()
        print(f"已回滚活动桌宠：{restored}")
        return 0
    workspace = workspace_path(args.pet_id)
    if args.activate_existing:
        config = load_config(workspace)
        runtime_root = workspace / str(config["runtime_directory"])
        report = json.loads(
            (
                workspace
                / str(config["review_directory"])
                / "assets-qa.json"
            ).read_text(encoding="utf-8-sig")
        )
    else:
        runtime_root, report = assemble_workspace(workspace)
        print(f"已生成 {report['frame_count']} 个运行帧：{runtime_root}")
        print(
            "动作检查表："
            f"{workspace / 'review' / 'all-actions-contact-sheet.jpg'}"
        )
    if args.activate or args.activate_existing:
        if os.environ.get("ONEPIC_TESTS_PASSED") != "1":
            raise RuntimeError(
                "激活前必须通过 scripts/test.ps1 -UnitOnly；"
                "请使用 scripts/build_pet.ps1 -Activate。"
            )
        backups = activate_workspace(workspace, runtime_root)
        print(f"已激活为当前桌宠：{ACTIVE_PET_ROOT}")
        print(f"上一只桌宠备份：{backups['previous_pet_backup'] or '无'}")
    else:
        print("尚未替换当前桌宠；确认检查表后使用 --activate。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
