"""
本模块加载并验证声明式动画配方。

配方定义 38 帧基础状态、复用和过渡规则，以及 0–2 张摸头过渡帧的运行策略。
验证失败时立即停止装配，确保输出状态顺序和帧数量保持确定性。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .resources import resource_path


class RecipeError(ValueError):
    """表示声明式动画配方不符合 OnePic 契约。"""


EXPECTED_STATES = (
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
)


def validate_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    """验证 schema 关键约束、引用和基础帧总数。"""

    if recipe.get("schema_version") != 1:
        raise RecipeError("动画配方 schema_version 必须为 1")
    order = recipe.get("state_order")
    if tuple(order or ()) != EXPECTED_STATES:
        raise RecipeError("动画状态顺序与 OnePic v1 契约不一致")
    animations = recipe.get("animations")
    if not isinstance(animations, dict) or tuple(animations) != EXPECTED_STATES:
        raise RecipeError("animations 必须按 state_order 完整声明")
    total = 0
    for state in EXPECTED_STATES:
        entry = animations[state]
        if not isinstance(entry, dict):
            raise RecipeError(f"{state} 配方必须是对象")
        kind = entry.get("kind")
        count = entry.get("count")
        if kind not in {"pose_sequence", "walk_input"}:
            raise RecipeError(f"{state} 使用未知 kind：{kind}")
        if not isinstance(count, int) or count < 1:
            raise RecipeError(f"{state} count 必须是正整数")
        if kind == "pose_sequence":
            frames = entry.get("frames")
            if not isinstance(frames, list) or len(frames) != count:
                raise RecipeError(f"{state} frames 数量必须等于 count")
            for frame in frames:
                if not isinstance(frame, dict) or not frame.get("source"):
                    raise RecipeError(f"{state} 每帧必须声明 source")
        total += count
    if total != recipe.get("base_frame_count") or total != 38:
        raise RecipeError(f"基础配方必须恰好 38 帧，当前为 {total}")

    touch = recipe.get("touch_feedback")
    if not isinstance(touch, dict):
        raise RecipeError("缺少 touch_feedback 配置")
    sources = touch.get("optional_transition_sources")
    maximum = touch.get("maximum_optional_frames")
    default_for_new = touch.get("default_for_new_pets")
    if not isinstance(sources, list) or maximum not in (0, 1, 2):
        raise RecipeError("摸头过渡帧必须声明 0–2 张")
    if default_for_new not in (0, 1, 2) or default_for_new > maximum:
        raise RecipeError("新宠物默认摸头过渡帧必须为 0–2 且不超过最大值")
    if len(sources) > maximum:
        raise RecipeError("摸头过渡素材超过 maximum_optional_frames")
    if touch.get("default_policy") not in {
        "fixed_shy",
        "fixed_happy",
        "alternate",
        "weighted",
        "weighted_no_repeat",
    }:
        raise RecipeError("未知摸头反馈分支策略")
    return recipe


def load_recipe(path: Path | None = None) -> dict[str, Any]:
    """从 UTF-8 JSON 读取并验证动画配方。"""

    target = path or resource_path("config/animation-recipe.v1.json")
    try:
        value = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(f"无法读取动画配方 {target}：{exc}") from exc
    if not isinstance(value, dict):
        raise RecipeError("动画配方根节点必须是对象")
    return validate_recipe(value)
