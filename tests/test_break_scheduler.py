"""验证桌宠内置休息提醒的纯配置、状态转换、隐私存储和边界行为。"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from onepic_desktop_pet.break_scheduler import (
    BreakReminderConfigError,
    BreakReminderStore,
    SchedulerEngine,
    canonical_break_reminder_manifest,
    normalize_break_reminder_config,
)


def _config(*, enabled: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "enabled": enabled,
        "schedule": {
            "preview_seconds": 2,
            "idle_threshold_seconds": 5,
            "resume_grace_seconds": 3,
            "pause_on_fullscreen": True,
            "reflow_on_idle": True,
            "quiet_hours": {
                "enabled": True,
                "start": "22:30",
                "end": "08:00",
            },
            "reminders": [
                {
                    "id": "micro",
                    "kind": "micro_break",
                    "label": "微休息",
                    "interval_minutes": 1,
                    "duration_seconds": 10,
                    "preview_seconds": 2,
                    "snooze_minutes": 0.5,
                },
                {
                    "id": "pomodoro",
                    "kind": "pomodoro",
                    "label": "25/5番茄钟",
                    "work_minutes": 25,
                    "break_minutes": 5,
                    "long_break_minutes": 15,
                    "focus_sessions_per_long_break": 4,
                    "duration_seconds": 300,
                    "preview_seconds": 30,
                    "snooze_minutes": 2,
                },
            ],
        },
        "sounds": {"enabled_default": False, "files": {}},
        "privacy": {
            "network_default": "off",
            "collect_window_titles": False,
            "statistics_enabled_default": False,
        },
        "test_mode": {
            "minute_seconds": 1,
            "preview_seconds": 1,
            "duration_seconds": 3,
        },
    }


def test_config_normalizes_health_and_existing_pomodoro_contract() -> None:
    config = normalize_break_reminder_config(_config(), test_mode=True)

    assert config.enabled is True
    assert [item.kind for item in config.definitions] == [
        "micro_break",
        "pomodoro",
    ]
    pomodoro = config.definitions[1]
    assert pomodoro.work_seconds == 25
    assert pomodoro.break_seconds == 5
    assert pomodoro.long_break_seconds == 15
    assert pomodoro.focus_sessions_per_long_break == 4
    assert config.sounds_enabled_default is False
    assert config.statistics_enabled_default is False

    manifest = canonical_break_reminder_manifest(_config(enabled=False))
    assert manifest["enabled"] is False
    assert manifest["privacy"] == {
        "network_default": "off",
        "collect_window_titles": False,
        "statistics_enabled_default": False,
    }
    assert manifest["schedule"]["reminders"][1]["long_break_minutes"] == 15


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data["schedule"]["quiet_hours"].update(start="29:00"), "HH:MM"),
        (lambda data: data["schedule"]["reminders"][0].update(id="../bad"), "id"),
        (lambda data: data["privacy"].update(network_default="on"), "不得联网"),
        (lambda data: data["sounds"].update(files={"reminder": "../x.wav"}), "不安全"),
    ],
)
def test_config_rejects_invalid_or_privacy_unsafe_values(mutation, message) -> None:
    data = _config()
    mutation(data)
    with pytest.raises(BreakReminderConfigError, match=message):
        normalize_break_reminder_config(data)


def test_scheduler_preview_active_snooze_skip_complete_and_pause_zero() -> None:
    definition = normalize_break_reminder_config(_config()).definitions[0]
    engine = SchedulerEngine([definition], now=0, quiet_hours={"enabled": False})

    assert [event.stage for event in engine.tick(0)] == []
    assert [event.stage for event in engine.tick(59)] == ["preview"]
    assert [event.stage for event in engine.tick(60)] == ["active"]
    assert engine.snooze(61).stage == "snooze"
    assert engine.snapshot(61)["reminders"]["micro"]["next_in_seconds"] == 30

    engine.trigger("micro", 70)
    assert engine.skip(71).stage == "skip"
    engine.trigger("micro", 80)
    assert engine.complete(81).stage == "complete"

    before = engine.snapshot(90)["reminders"]["micro"]["next_in_seconds"]
    engine.pause(0)
    engine.resume(5)
    after = engine.snapshot(90)["reminders"]["micro"]["next_in_seconds"]
    assert after == before + 5


def test_fullscreen_defers_active_and_unblock_reflows_without_burst() -> None:
    definition = normalize_break_reminder_config(_config()).definitions[0]
    engine = SchedulerEngine(
        [definition],
        now=0,
        quiet_hours={"enabled": False},
        resume_grace_seconds=3,
    )
    engine.trigger("micro", 1)

    events = engine.tick(2, fullscreen=True)
    assert [event.stage for event in events] == ["deferred"]
    assert engine.active_reminder_id is None
    assert engine.blocked_reason == "fullscreen"

    events = engine.tick(3, fullscreen=False)
    assert [event.stage for event in events] == ["reflow"]
    assert events[0].reason == "fullscreen"
    assert [event.stage for event in engine.tick(3.1)] == ["preview"]


def test_quiet_hours_and_long_tick_gap_reflow() -> None:
    definition = normalize_break_reminder_config(_config()).definitions[0]
    engine = SchedulerEngine(
        [definition],
        now=0,
        quiet_hours={"enabled": True, "start": "22:30", "end": "08:00"},
        idle_threshold_seconds=5,
    )
    assert engine.tick(
        1,
        wall_now=datetime(2026, 8, 14, 23, 0),
    ) == []
    assert engine.blocked_reason == "quiet_hours"
    assert engine.tick(
        2,
        wall_now=datetime(2026, 8, 15, 9, 0),
    )[0].stage == "reflow"

    event = engine.tick(
        100,
        wall_now=datetime(2026, 8, 15, 9, 1),
    )[0]
    assert event.stage == "reflow"
    assert event.reason == "long_tick_gap"


def test_local_store_never_counts_until_user_enables_statistics(tmp_path) -> None:
    path = tmp_path / "break-reminders.json"
    store = BreakReminderStore(path)
    store.increment("demo", "complete", enabled=False)
    assert not path.exists()

    store.set_setting("demo", "statistics_enabled", True)
    store.increment("demo", "complete", enabled=True)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["profiles"]["demo"]["statistics"] == {"complete": 1}
    serialized = json.dumps(saved, ensure_ascii=False)
    assert "window_title" not in serialized
    assert "keystroke" not in serialized
