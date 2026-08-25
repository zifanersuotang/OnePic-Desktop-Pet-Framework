"""
本模块验证纯本地桌宠台词的清理、概率、冷却、防重复和按宠物覆盖规则。

测试不创建 GUI、不访问网络、不读取用户真实配置，也不写工作区外文件。
"""

import random

from onepic_desktop_pet.dialogue import (
    DialogueSelector,
    normalize_dialogue_config,
    sanitize_dialogue_overrides,
)


def test_dialogue_config_sanitizes_duplicate_and_overlong_lines() -> None:
    config = normalize_dialogue_config(
        {
            "enabled": True,
            "max_text_length": 8,
            "lines": {
                "wave": ["  你好   呀  ", "你好 呀", "", "1234567890"],
            },
        }
    )

    assert config["lines"]["wave"] == ["你好 呀", "12345678"]
    assert config["max_text_length"] == 8


def test_dialogue_selector_avoids_recent_repeats_and_obeys_cooldown() -> None:
    selector = DialogueSelector(
        {
            "enabled": True,
            "cooldown_ms": 1000,
            "no_repeat_window": 2,
            "lines": {"wave": ["一", "二", "三"]},
        },
        rng=random.Random(7),
    )

    first = selector.choose("wave", source="manual", now=0, force=True)
    assert selector.choose(
        "wave",
        source="manual",
        now=0.5,
        force=True,
    ) is None
    second = selector.choose("wave", source="manual", now=1.1, force=True)
    third = selector.choose("wave", source="manual", now=2.2, force=True)

    assert len({first, second, third}) == 3


def test_empty_user_override_disables_one_action_without_affecting_others() -> None:
    selector = DialogueSelector(
        {
            "enabled": True,
            "cooldown_ms": 0,
            "lines": {"wave": ["默认挥手"], "happy": ["默认开心"]},
        },
        overrides={"wave": []},
    )

    assert selector.choose("wave", source="manual", force=True) is None
    assert (
        selector.choose("happy", source="manual", force=True)
        == "默认开心"
    )


def test_dialogue_overrides_are_scoped_and_sanitized_per_pet() -> None:
    overrides = sanitize_dialogue_overrides(
        {
            "gugugaga2": {
                "wave": [" 自定义一 ", "自定义一", "自定义二"],
                "idle": [],
            },
            "": {"wave": ["不得保留"]},
        }
    )

    assert overrides == {
        "gugugaga2": {
            "wave": ["自定义一", "自定义二"],
            "idle": [],
        }
    }
