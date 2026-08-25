"""
本模块负责桌面宠物默认配置、用户配置、尺寸、滚轮缩放、位置、活动区域、走路样式、
自定义台词和可选养成模拟开关的加载与保存。

职责范围：
- 从项目内只读 JSON 读取默认功能设置；
- 从当前用户本地应用数据目录读取上次窗口位置、显示尺寸、滚轮缩放模式、可选活动区域、
  走路样式、按宠物分组的台词和养成存档；
- 校验窗口、移动、动画和转身节奏的数值范围并忽略未知字段；
- 仅在用户配置目录保存用户偏好，其他体验参数始终采用项目默认值。

Agent 快速定位：
- 配置数据结构位于 PetSettings；
- 合并和校验逻辑位于 load_settings()；
- 持久化入口位于 save_settings()；
- 不应把机器相关的绝对路径写入项目默认配置。

输入为 JSON 文件，输出为 PetSettings 实例。保存操作会创建用户配置目录并原子写入
`start_x`、`start_y`、`display_height`、`wheel_resize_mode`、活动区域、`pet_id`、
`walk_variant`、`dialogue_overrides` 与 `care_states`。测试或便携烟测可通过
`ONEPIC_SETTINGS_DIR` 重定向设置目录，不会覆盖项目默认配置，也不访问网络。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .care import PetCareState
from .resources import resource_path
from .dialogue import sanitize_dialogue_overrides


@dataclass
class PetSettings:
    """保存桌面宠物可配置参数和上次窗口位置。"""

    display_height: int = 220
    wheel_resize_mode: str = "ctrl"
    movement_interval_ms: int = 16
    movement_step: int = 1
    walk_frame_interval_ms: int = 90
    turn_pause_ms: int = 240
    idle_min_ms: int = 3000
    idle_max_ms: int = 7000
    action_min_ms: int = 3500
    action_max_ms: int = 7000
    inactive_sit_ms: int = 300000
    inactive_sleep_ms: int = 600000
    always_on_top: bool = True
    care_simulation_enabled: bool = False
    movement_region_enabled: bool = False
    movement_region: tuple[int, int, int, int] | None = None
    start_x: int | None = None
    start_y: int | None = None
    pet_id: str | None = None
    walk_variant: str | None = None
    dialogue_overrides: dict[str, dict[str, list[str]]] = field(
        default_factory=dict
    )
    care_states: dict[str, dict[str, float | int]] = field(default_factory=dict)


def user_settings_path() -> Path:
    """返回当前用户可写的设置文件路径。"""

    override = os.environ.get("ONEPIC_SETTINGS_DIR")
    if override:
        return Path(override).expanduser().resolve() / "settings.json"
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".desktop_pet"
    return root / "OnePicDesktopPet" / "settings.json"


def _read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；文件不存在时返回空对象。"""

    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取配置文件 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"配置文件必须包含 JSON 对象：{path}")
    return value


def _validated(data: dict[str, Any]) -> PetSettings:
    """过滤未知字段并对关键数值执行安全范围校验。"""

    allowed = {field.name for field in fields(PetSettings)}
    clean = {key: value for key, value in data.items() if key in allowed}
    settings = PetSettings(**clean)
    settings.display_height = min(600, max(120, int(settings.display_height)))
    settings.wheel_resize_mode = str(settings.wheel_resize_mode).strip().lower()
    if settings.wheel_resize_mode not in {"disabled", "direct", "ctrl"}:
        settings.wheel_resize_mode = "ctrl"
    settings.movement_interval_ms = min(
        100,
        max(16, int(settings.movement_interval_ms)),
    )
    settings.movement_step = min(12, max(1, int(settings.movement_step)))
    settings.walk_frame_interval_ms = min(
        500,
        max(50, int(settings.walk_frame_interval_ms)),
    )
    settings.turn_pause_ms = min(1200, max(0, int(settings.turn_pause_ms)))
    settings.idle_min_ms = max(500, int(settings.idle_min_ms))
    settings.idle_max_ms = max(settings.idle_min_ms, int(settings.idle_max_ms))
    settings.action_min_ms = max(1000, int(settings.action_min_ms))
    settings.action_max_ms = max(
        settings.action_min_ms,
        int(settings.action_max_ms),
    )
    settings.inactive_sit_ms = max(5000, int(settings.inactive_sit_ms))
    settings.inactive_sleep_ms = max(
        settings.inactive_sit_ms + 5000,
        int(settings.inactive_sleep_ms),
    )
    settings.care_simulation_enabled = bool(settings.care_simulation_enabled)
    settings.movement_region_enabled = bool(settings.movement_region_enabled)
    raw_region = settings.movement_region
    if isinstance(raw_region, (list, tuple)) and len(raw_region) == 4:
        try:
            x, y, width, height = (int(value) for value in raw_region)
        except (TypeError, ValueError):
            settings.movement_region = None
        else:
            settings.movement_region = (
                min(100000, max(-100000, x)),
                min(100000, max(-100000, y)),
                min(20000, max(120, width)),
                min(20000, max(120, height)),
            )
    else:
        settings.movement_region = None
    if settings.pet_id is not None:
        settings.pet_id = str(settings.pet_id).strip() or None
    if settings.walk_variant is not None:
        settings.walk_variant = str(settings.walk_variant).strip() or None
    settings.dialogue_overrides = sanitize_dialogue_overrides(
        settings.dialogue_overrides
    )
    raw_care_states = (
        settings.care_states if isinstance(settings.care_states, dict) else {}
    )
    settings.care_states = {
        pet_id: PetCareState.from_dict(value).to_dict()
        for raw_pet_id, value in raw_care_states.items()
        if (pet_id := str(raw_pet_id).strip()) and len(pet_id) <= 80
    }
    return settings


def load_settings(
    default_path: Path | None = None,
    override_path: Path | None = None,
) -> PetSettings:
    """合并默认与用户配置；损坏的用户配置回退为默认配置。"""

    default_file = default_path or resource_path("config/settings.json")
    user_file = override_path or user_settings_path()
    base = _read_json(default_file)
    try:
        override = _read_json(user_file)
    except ValueError:
        override = {}
    base.update(
        {
            key: value
            for key, value in override.items()
            if key
            in {
                "display_height",
                "wheel_resize_mode",
                "movement_region_enabled",
                "movement_region",
                "start_x",
                "start_y",
                "pet_id",
                "walk_variant",
                "dialogue_overrides",
                "care_states",
            }
        }
    )
    return _validated(base)


def save_settings(settings: PetSettings, path: Path | None = None) -> Path:
    """将设置原子写入用户目录并返回最终路径。"""

    target = path or user_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    state = {
        "display_height": settings.display_height,
        "wheel_resize_mode": settings.wheel_resize_mode,
        "movement_region_enabled": settings.movement_region_enabled,
        "movement_region": settings.movement_region,
        "start_x": settings.start_x,
        "start_y": settings.start_y,
        "pet_id": settings.pet_id,
        "walk_variant": settings.walk_variant,
        "dialogue_overrides": settings.dialogue_overrides,
        "care_states": settings.care_states,
    }
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
