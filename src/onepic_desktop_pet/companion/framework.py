"""
本模块定义 OnePic 智能陪伴层的稳定功能注册表与安全边界。

职责范围：
- 用不可变声明描述人物档案、历史记忆、聊天、计时工具和小游戏能力；
- 标明每项能力是否联网、是否默认启用以及会处理哪类本地数据；
- 提供原创的默认预设指令，不复制参考商品的角色文案；
- 规范化双击快捷聊天界面，并保证打开窗口本身永不触发联网；
- 统一验证宠物标识，保证所有持久化状态继续按 ``pet_id`` 隔离。

本模块不创建窗口、不访问网络、不读写文件，也不加载任何角色素材。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from copy import deepcopy
from typing import Final


FRAMEWORK_SCHEMA_VERSION: Final[int] = 1
_PET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
DEFAULT_COMPANION_UI_CONFIG: Final[dict[str, object]] = {
    "double_click_chat": True,
    "compact_chat": True,
    "reply_bubble_style": "crystal",
    "network_on_open": False,
}
_COMPANION_UI_KEYS: Final[frozenset[str]] = frozenset(
    DEFAULT_COMPANION_UI_CONFIG
)


@dataclass(frozen=True, slots=True)
class CompanionFeature:
    """描述一项可独立测试、启用和审计的陪伴能力。"""

    key: str
    label: str
    category: str
    default_enabled: bool
    requires_network: bool
    local_data: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PresetCommand:
    """描述一条只向聊天模型提供文本、不能执行代码的预设指令。"""

    key: str
    label: str
    prompt: str


FEATURES: Final[tuple[CompanionFeature, ...]] = (
    CompanionFeature(
        "profiles",
        "人设与委托人档案",
        "identity",
        True,
        False,
        ("character_profile", "user_profile"),
    ),
    CompanionFeature(
        "history",
        "聊天历史",
        "memory",
        True,
        False,
        ("messages",),
    ),
    CompanionFeature(
        "memory",
        "可编辑记忆",
        "memory",
        True,
        False,
        ("memory_facts",),
    ),
    CompanionFeature(
        "preset_commands",
        "预设指令",
        "chat",
        True,
        False,
        ("preset_commands",),
    ),
    CompanionFeature(
        "ai_chat",
        "用户自备 API 实时聊天",
        "chat",
        False,
        True,
        ("messages", "selected_memories", "profiles"),
    ),
    CompanionFeature(
        "focus_timer",
        "正倒计时与番茄钟",
        "tools",
        True,
        False,
        ("timer_preferences",),
    ),
    CompanionFeature(
        "hourly_chime",
        "整点报时",
        "tools",
        False,
        False,
        ("tool_preferences",),
    ),
    CompanionFeature(
        "random_number",
        "范围随机数",
        "tools",
        True,
        False,
    ),
    CompanionFeature(
        "minesweeper",
        "扫雷",
        "games",
        True,
        False,
        ("game_preferences",),
    ),
    CompanionFeature(
        "affection_bridge",
        "好感度语气与动作桥接",
        "integration",
        True,
        False,
        ("bond",),
    ),
)


DEFAULT_PRESET_COMMANDS: Final[tuple[PresetCommand, ...]] = (
    PresetCommand(
        "today_plan",
        "一起整理今日计划",
        "结合当前档案和已选记忆，陪我整理今天最重要的三件事，语气自然简洁。",
    ),
    PresetCommand(
        "daily_chat",
        "聊聊今天",
        "像熟悉的伙伴一样询问我今天过得怎样，并根据我的回答继续对话。",
    ),
    PresetCommand(
        "memory_review",
        "回顾共同记忆",
        "只根据我明确选中的记忆做一次温和回顾；没有记忆时直接说明，不要虚构。",
    ),
)


def normalize_companion_ui_config(value: object) -> dict[str, object]:
    """返回 Windows 快捷聊天 UI 配置，并硬性禁止打开即联网。"""

    if value is None:
        return deepcopy(DEFAULT_COMPANION_UI_CONFIG)
    if not isinstance(value, dict):
        raise ValueError("companion_ui 必须是 JSON 对象")
    unknown = sorted(set(value) - _COMPANION_UI_KEYS)
    if unknown:
        raise ValueError("companion_ui 含未知字段：" + ", ".join(unknown))

    result = deepcopy(DEFAULT_COMPANION_UI_CONFIG)
    for key in ("double_click_chat", "compact_chat", "network_on_open"):
        if key not in value:
            continue
        if not isinstance(value[key], bool):
            raise ValueError(f"companion_ui.{key} 必须是布尔值")
        result[key] = value[key]
    if result["network_on_open"]:
        raise ValueError("companion_ui.network_on_open 必须为 false")

    if "reply_bubble_style" in value:
        style = value["reply_bubble_style"]
        if style != "crystal":
            raise ValueError(
                "companion_ui.reply_bubble_style 当前只支持 crystal"
            )
        result["reply_bubble_style"] = style
    return result


def normalize_pet_id(value: object) -> str:
    """返回安全的宠物标识；非法或过长输入直接拒绝。"""

    pet_id = str(value or "").strip()
    if not _PET_ID_PATTERN.fullmatch(pet_id):
        raise ValueError("pet_id 只能包含字母、数字、点、下划线和连字符，最长 80 字符")
    return pet_id


def feature_manifest() -> dict[str, object]:
    """返回可序列化且顺序稳定的功能清单。"""

    return {
        "schema_version": FRAMEWORK_SCHEMA_VERSION,
        "features": [asdict(feature) for feature in FEATURES],
        "default_presets": [asdict(command) for command in DEFAULT_PRESET_COMMANDS],
    }
