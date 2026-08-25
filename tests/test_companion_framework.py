"""
本模块验证智能陪伴功能注册表、默认指令和宠物隔离标识约束。

测试只处理内存数据，不访问网络、用户配置或角色素材。
"""

from __future__ import annotations

import pytest

from onepic_desktop_pet.companion.framework import (
    DEFAULT_COMPANION_UI_CONFIG,
    DEFAULT_PRESET_COMMANDS,
    FEATURES,
    feature_manifest,
    normalize_companion_ui_config,
    normalize_pet_id,
)
from tools.smoke_double_click_chat import rendering_is_ready


def test_feature_registry_has_unique_keys_and_network_is_opt_in() -> None:
    keys = [feature.key for feature in FEATURES]
    assert len(keys) == len(set(keys))
    ai_chat = next(feature for feature in FEATURES if feature.key == "ai_chat")
    assert ai_chat.requires_network is True
    assert ai_chat.default_enabled is False


def test_default_presets_are_original_text_only_commands() -> None:
    assert len(DEFAULT_PRESET_COMMANDS) == 3
    assert all(command.prompt.strip() for command in DEFAULT_PRESET_COMMANDS)
    assert all("http" not in command.prompt.lower() for command in DEFAULT_PRESET_COMMANDS)


def test_pet_id_normalization_fails_closed() -> None:
    assert normalize_pet_id(" pet-01 ") == "pet-01"
    for value in ("", "../private", "含中文", "a" * 81):
        with pytest.raises(ValueError):
            normalize_pet_id(value)


def test_manifest_is_serializable_shape() -> None:
    manifest = feature_manifest()
    assert manifest["schema_version"] == 1
    assert {item["key"] for item in manifest["features"]} == {
        feature.key for feature in FEATURES
    }


def test_companion_ui_defaults_to_compact_double_click_without_network() -> None:
    config = normalize_companion_ui_config(None)

    assert config == DEFAULT_COMPANION_UI_CONFIG
    assert config is not DEFAULT_COMPANION_UI_CONFIG
    assert config["double_click_chat"] is True
    assert config["compact_chat"] is True
    assert config["reply_bubble_style"] == "crystal"
    assert config["network_on_open"] is False


def test_double_click_smoke_waits_for_live2d_only_on_hybrid_route() -> None:
    """纯精灵窗口不能因为 `_live2d_ready` 为假而被烟测误判为超时。"""

    assert rendering_is_ready({"rendering_mode": "sprite"}, False) is True
    assert (
        rendering_is_ready({"rendering_mode": "hybrid-live2d"}, False)
        is False
    )
    assert (
        rendering_is_ready({"rendering_mode": "hybrid-live2d"}, True)
        is True
    )


@pytest.mark.parametrize(
    "value",
    [
        {"network_on_open": True},
        {"double_click_chat": "yes"},
        {"compact_chat": 1},
        {"reply_bubble_style": "plain"},
        {"unknown": False},
    ],
)
def test_companion_ui_rejects_unsafe_or_unknown_values(value: object) -> None:
    with pytest.raises(ValueError):
        normalize_companion_ui_config(value)
