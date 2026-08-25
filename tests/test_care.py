"""
本模块测试 VPet 类养成模拟的时间推进、互动反馈、边界约束与行为压力输出。

测试只运行纯 Python 逻辑，不读写用户存档，不创建窗口，也不访问网络。
"""

import pytest

from onepic_desktop_pet.care import (
    PetCareState,
    care_config_sha256,
    normalize_care_config,
)


def test_walk_consumes_vitality_and_builds_experience() -> None:
    care = PetCareState()

    care.advance(600, "walk")

    assert care.satiety == pytest.approx(79.2)
    assert care.hydration == pytest.approx(78.8)
    assert care.vitality == pytest.approx(73.2)
    assert care.experience == pytest.approx(0.6)


def test_sleep_restores_vitality_without_exceeding_bounds() -> None:
    care = PetCareState(vitality=99.0)

    care.advance(600, "sleep")

    assert care.vitality == 100.0
    assert 0 <= care.happiness <= 100


def test_food_drink_and_comfort_are_independent_events() -> None:
    care = PetCareState(
        satiety=20,
        hydration=25,
        happiness=30,
        bond=40,
    )

    care.feed(15)
    care.drink(20)
    care.comfort(10)

    assert care.satiety == 35
    assert care.hydration == 45
    assert care.happiness > 40
    assert care.bond == pytest.approx(43.5)
    assert care.experience > 0


def test_low_needs_produce_normalized_behavior_pressure() -> None:
    care = PetCareState(
        satiety=5,
        hydration=8,
        vitality=10,
        happiness=12,
    )

    influence = care.influence()

    assert 0 <= influence.rest_pressure <= 1
    assert 0 <= influence.physical_pressure <= 1
    assert 0 <= influence.social_pressure <= 1
    assert influence.physical_pressure >= influence.rest_pressure


def test_care_state_round_trip_clamps_untrusted_values() -> None:
    state = PetCareState.from_dict(
        {
            "satiety": 150,
            "hydration": -10,
            "experience": "125.5",
            "coins": -8,
        },
        initial_coins=75,
    )

    assert state.satiety == 100
    assert state.hydration == 0
    assert state.experience == 125.5
    assert state.level == 2
    assert state.coins == 0
    assert PetCareState.from_dict({}, initial_coins=75).coins == 75


def test_play_spending_and_focus_reward_form_a_small_care_loop() -> None:
    state = PetCareState(vitality=40, happiness=30, coins=10)

    assert state.try_spend(8) is True
    state.feed(20)
    assert state.play(vitality_cost=6, happiness_gain=12) is True
    state.reward(coins=18, experience=10)

    assert state.coins == 20
    assert state.vitality == 34
    assert state.happiness > 42
    assert state.experience > 10


def test_care_config_is_normalized_and_semantically_hashed() -> None:
    raw = {
        "enabled": True,
        "initial_coins": 88,
        "feed_cost": "9",
        "action_cooldown_ms": 1,
    }

    normalized = normalize_care_config(raw)

    assert normalized["initial_coins"] == 88
    assert normalized["feed_cost"] == 9
    assert normalized["action_cooldown_ms"] == 200
    assert care_config_sha256(raw) == care_config_sha256(normalized)
