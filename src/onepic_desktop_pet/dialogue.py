"""
本模块实现桌宠本地台词配置的校验、用户覆盖清理和防重复随机选择。

职责范围：
- 规范化运行清单中的 `dialogue_system`；
- 限制动作数量、单组句数和单句长度，过滤空行与重复内容；
- 按手动、摸头、陪伴和自主行为概率选句；
- 使用全局冷却与最近句窗口减少刷屏和机械重复；
- 清理按宠物保存的用户自定义台词。

本模块只处理纯文本和内存状态，不创建窗口、不读写文件、不联网，也不调用 AI。
"""

from __future__ import annotations

import random
import time
from collections import deque
from typing import Any


DEFAULT_ACTION_LABELS = {
    "idle": "待机",
    "walk": "正面走路",
    "side_walk_v1": "侧面走路",
    "wave": "挥手",
    "happy": "开心",
    "shy": "害羞",
    "surprised": "惊讶",
    "annoyed": "生气",
    "sleepy": "困倦",
    "curious": "好奇",
    "selfie": "自拍",
    "touch": "摸头",
    "cursor_hug": "抱住鼠标",
    "typing": "陪打字",
    "focus_start": "开始专注",
    "focus_complete": "完成专注",
    "signature_wave": "招牌动作",
    "focus_work": "专注工作",
    "jump": "开心跳跃",
    "wait_response": "等待回应",
    "micro_expressions_v1": "微表情",
}

DEFAULT_CHANCES = {
    "manual": 1.0,
    "touch": 0.75,
    "companion": 0.55,
    "autonomous": 0.14,
}


def sanitize_dialogue_lines(
    value: object,
    *,
    max_length: int = 60,
    max_lines: int = 24,
) -> list[str]:
    """清理一组台词，保序去重并限制长度与数量。"""

    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item).split())
        if not text:
            continue
        text = text[:max_length]
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= max_lines:
            break
    return result


def normalize_dialogue_config(raw: object) -> dict[str, Any]:
    """把清单配置转换为有安全边界的运行时结构。"""

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("dialogue_system 必须是 JSON 对象")
    max_text_length = min(120, max(8, int(raw.get("max_text_length", 60))))
    max_lines = min(50, max(1, int(raw.get("max_lines_per_action", 24))))
    raw_lines = raw.get("lines", {})
    if not isinstance(raw_lines, dict):
        raise ValueError("dialogue_system.lines 必须是 JSON 对象")
    lines: dict[str, list[str]] = {}
    for raw_name, raw_values in list(raw_lines.items())[:48]:
        name = str(raw_name).strip()
        if not name:
            continue
        clean = sanitize_dialogue_lines(
            raw_values,
            max_length=max_text_length,
            max_lines=max_lines,
        )
        if clean:
            lines[name] = clean

    labels = dict(DEFAULT_ACTION_LABELS)
    raw_labels = raw.get("action_labels", {})
    if not isinstance(raw_labels, dict):
        raise ValueError("dialogue_system.action_labels 必须是 JSON 对象")
    for raw_name, raw_label in list(raw_labels.items())[:48]:
        name = str(raw_name).strip()
        label = " ".join(str(raw_label).split())[:32]
        if name and label:
            labels[name] = label

    chances = dict(DEFAULT_CHANCES)
    raw_chances = raw.get("speak_chance", {})
    if not isinstance(raw_chances, dict):
        raise ValueError("dialogue_system.speak_chance 必须是 JSON 对象")
    for source in DEFAULT_CHANCES:
        if source in raw_chances:
            chances[source] = min(
                1.0,
                max(0.0, float(raw_chances[source])),
            )

    return {
        "enabled": bool(raw.get("enabled", False)),
        "bubble_duration_ms": min(
            10000,
            max(800, int(raw.get("bubble_duration_ms", 3400))),
        ),
        "cooldown_ms": min(
            60000,
            max(0, int(raw.get("cooldown_ms", 2400))),
        ),
        "no_repeat_window": min(
            12,
            max(0, int(raw.get("no_repeat_window", 3))),
        ),
        "max_text_length": max_text_length,
        "max_lines_per_action": max_lines,
        "speak_chance": chances,
        "action_labels": labels,
        "lines": lines,
    }


def sanitize_dialogue_overrides(raw: object) -> dict[str, dict[str, list[str]]]:
    """清理按宠物分组的用户覆盖，空数组保留为显式禁用。"""

    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, list[str]]] = {}
    for raw_pet_id, raw_actions in list(raw.items())[:32]:
        pet_id = str(raw_pet_id).strip()[:64]
        if not pet_id or not isinstance(raw_actions, dict):
            continue
        actions: dict[str, list[str]] = {}
        for raw_action, raw_lines in list(raw_actions.items())[:48]:
            action = str(raw_action).strip()[:64]
            if not action or not isinstance(raw_lines, (list, tuple)):
                continue
            actions[action] = sanitize_dialogue_lines(
                raw_lines,
                max_length=120,
                max_lines=50,
            )
        if actions:
            result[pet_id] = actions
    return result


class DialogueSelector:
    """按台词池、概率、冷却和最近历史选择一句本地文本。"""

    def __init__(
        self,
        config: dict[str, Any],
        overrides: dict[str, list[str]] | None = None,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self.config = normalize_dialogue_config(config)
        self.overrides = dict(overrides or {})
        self.rng = rng or random.Random()
        self._recent: deque[str] = deque(
            maxlen=int(self.config["no_repeat_window"])
        )
        self._last_by_action: dict[str, str] = {}
        self._last_shown_at = float("-inf")

    def lines_for(self, action: str) -> list[str]:
        """返回用户覆盖或角色默认台词，空覆盖表示禁用该动作。"""

        if action in self.overrides:
            return list(self.overrides[action])
        return list(self.config["lines"].get(action, []))

    def choose(
        self,
        action: str,
        *,
        source: str,
        now: float | None = None,
        force: bool = False,
        ignore_cooldown: bool = False,
    ) -> str | None:
        """为一次动作选择台词；无可用句、概率或冷却未通过时返回 None。"""

        if not self.config["enabled"]:
            return None
        lines = self.lines_for(action)
        if not lines:
            return None
        current = time.monotonic() if now is None else float(now)
        cooldown = float(self.config["cooldown_ms"]) / 1000
        if not ignore_cooldown and current - self._last_shown_at < cooldown:
            return None
        chance = float(
            self.config["speak_chance"].get(
                source,
                self.config["speak_chance"]["autonomous"],
            )
        )
        if not force and self.rng.random() > chance:
            return None
        recent = set(self._recent)
        last_action_line = self._last_by_action.get(action)
        candidates = [
            line
            for line in lines
            if line not in recent and line != last_action_line
        ]
        if not candidates:
            candidates = [line for line in lines if line != last_action_line]
        if not candidates:
            candidates = lines
        selected = self.rng.choice(candidates)
        self._last_shown_at = current
        self._last_by_action[action] = selected
        self._recent.append(selected)
        return selected
