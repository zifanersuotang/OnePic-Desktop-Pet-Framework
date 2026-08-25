"""
本模块测试桌面宠物配置的合并、滚轮缩放模式、活动区域、走路样式、自定义台词、
养成模拟开关、按宠物养成存档、范围校验和原子保存行为。

测试只使用 pytest 提供的临时目录，不写入真实用户配置目录，不修改项目默认配置，
也不创建 GUI 或网络请求。
"""

import json
from pathlib import Path

from onepic_desktop_pet.config import (
    PetSettings,
    load_settings,
    save_settings,
    user_settings_path,
)


def test_load_settings_merges_position_and_user_selected_size(tmp_path) -> None:
    default_path = tmp_path / "default.json"
    override_path = tmp_path / "override.json"
    default_path.write_text(
        json.dumps({"display_height": 280, "movement_step": 3}),
        encoding="utf-8",
    )
    override_path.write_text(
        json.dumps(
            {
                "display_height": 9999,
                "wheel_resize_mode": "direct",
                "movement_region_enabled": True,
                "movement_region": [-320, 40, 860, 520],
                "movement_step": 0,
                "start_x": 25,
                "start_y": 40,
                "pet_id": "black-boots",
                "walk_variant": "side_walk_v1",
                "dialogue_overrides": {
                    "black-boots": {"wave": ["你好", "再见"]}
                },
                "care_states": {
                    "black-boots": {
                        "satiety": 42,
                        "coins": -5,
                        "experience": "12.5",
                    }
                },
                "unknown": 1,
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(default_path, override_path)

    assert settings.display_height == 600
    assert settings.wheel_resize_mode == "direct"
    assert settings.movement_region_enabled is True
    assert settings.movement_region == (-320, 40, 860, 520)
    assert settings.movement_step == 3
    assert settings.start_x == 25
    assert settings.start_y == 40
    assert settings.pet_id == "black-boots"
    assert settings.walk_variant == "side_walk_v1"
    assert settings.dialogue_overrides == {
        "black-boots": {"wave": ["你好", "再见"]}
    }
    assert settings.care_states["black-boots"]["satiety"] == 42
    assert settings.care_states["black-boots"]["coins"] == 0
    assert settings.care_states["black-boots"]["experience"] == 12.5
    assert not hasattr(settings, "unknown")


def test_broken_user_config_falls_back_to_defaults(tmp_path) -> None:
    default_path = tmp_path / "default.json"
    override_path = tmp_path / "override.json"
    default_path.write_text('{"movement_step": 5}', encoding="utf-8")
    override_path.write_text("not-json", encoding="utf-8")

    assert load_settings(default_path, override_path).movement_step == 5


def test_invalid_wheel_resize_mode_falls_back_to_ctrl(tmp_path) -> None:
    """未知滚轮模式必须回退到低误触的 Ctrl 组合键。"""

    default_path = tmp_path / "default.json"
    override_path = tmp_path / "override.json"
    default_path.write_text("{}", encoding="utf-8")
    override_path.write_text(
        json.dumps({"wheel_resize_mode": "unexpected"}),
        encoding="utf-8",
    )

    assert load_settings(default_path, override_path).wheel_resize_mode == "ctrl"


def test_movement_region_is_normalized_and_invalid_shape_is_ignored(
    tmp_path,
) -> None:
    """活动矩形应限制危险范围，格式错误时安全回退为未设置。"""

    default_path = tmp_path / "default.json"
    default_path.write_text("{}", encoding="utf-8")
    override_path = tmp_path / "override.json"
    override_path.write_text(
        json.dumps(
            {
                "movement_region_enabled": True,
                "movement_region": [-999999, 999999, 20, 999999],
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(default_path, override_path)
    assert settings.movement_region_enabled is True
    assert settings.movement_region == (-100000, 100000, 120, 20000)

    override_path.write_text(
        json.dumps({"movement_region": [1, 2, 3]}),
        encoding="utf-8",
    )
    assert load_settings(default_path, override_path).movement_region is None


def test_animation_timing_is_limited_to_safe_ranges(tmp_path) -> None:
    """动画节奏参数过大或过小时应被限制，避免计时器异常。"""

    default_path = tmp_path / "default.json"
    default_path.write_text(
        json.dumps({"walk_frame_interval_ms": 1, "turn_pause_ms": 9999}),
        encoding="utf-8",
    )

    settings = load_settings(default_path, tmp_path / "missing.json")

    assert settings.walk_frame_interval_ms == 50
    assert settings.turn_pause_ms == 1200


def test_default_inactivity_uses_five_and_ten_minutes() -> None:
    """默认无互动阈值应为五分钟坐下、十分钟睡觉。"""

    settings = PetSettings()
    assert settings.inactive_sit_ms == 300000
    assert settings.inactive_sleep_ms == 600000


def test_repository_defaults_keep_movement_region_opt_in() -> None:
    """所有新构建都应带入口但默认关闭，且不得携带制作者屏幕坐标。"""

    default_path = Path(__file__).resolve().parents[1] / "config" / "settings.json"
    data = json.loads(default_path.read_text(encoding="utf-8"))

    assert data["movement_region_enabled"] is False
    assert data["movement_region"] is None


def test_care_simulation_is_opt_in() -> None:
    """VPet 类养成层默认关闭，避免改变既有宠物体验。"""

    assert PetSettings().care_simulation_enabled is False


def test_save_settings_writes_json(tmp_path) -> None:
    path = tmp_path / "nested" / "settings.json"
    saved = save_settings(
        PetSettings(
            start_x=12,
            start_y=34,
            wheel_resize_mode="direct",
            movement_region_enabled=True,
            movement_region=(-200, 80, 900, 560),
            pet_id="desk-follow",
            walk_variant="side_walk_v1",
            dialogue_overrides={
                "desk-follow": {"wave": ["自定义问候"]}
            },
            care_states={
                "desk-follow": {
                    "satiety": 65,
                    "hydration": 72,
                    "coins": 33,
                }
            },
        ),
        path,
    )

    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["start_x"] == 12
    assert data["start_y"] == 34
    assert data["display_height"] == 220
    assert data["wheel_resize_mode"] == "direct"
    assert data["movement_region_enabled"] is True
    assert data["movement_region"] == [-200, 80, 900, 560]
    assert data["pet_id"] == "desk-follow"
    assert data["walk_variant"] == "side_walk_v1"
    assert data["dialogue_overrides"] == {
        "desk-follow": {"wave": ["自定义问候"]}
    }
    assert data["care_states"]["desk-follow"]["satiety"] == 65
    assert data["care_states"]["desk-follow"]["coins"] == 33
    assert set(data) == {
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
    assert not path.with_suffix(".json.tmp").exists()


def test_settings_directory_can_be_redirected_for_smoke_tests(
    tmp_path,
    monkeypatch,
) -> None:
    """便携烟测必须能完全避开 LOCALAPPDATA。"""

    monkeypatch.setenv("ONEPIC_SETTINGS_DIR", str(tmp_path / "portable"))
    assert user_settings_path() == (tmp_path / "portable" / "settings.json").resolve()
