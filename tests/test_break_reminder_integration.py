"""离屏验证休息提醒控制器、真实 Qt 操作卡和普通桌宠安全视觉桥。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

from PySide6.QtWidgets import QApplication, QMenu, QWidget

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.break_reminder_ui import BreakReminderController
from onepic_desktop_pet.break_scheduler import BreakReminderStore
from onepic_desktop_pet.companion.tools import PomodoroPhase, PomodoroState
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.window import PetWindow


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakePet(QWidget):
    def __init__(self, manifest_path: Path) -> None:
        super().__init__()
        self.manifest_path = manifest_path
        self.presented: list[tuple[object, object]] = []
        self.setFixedSize(180, 220)

    def break_reminder_config(self):
        return {
            "schema_version": 1,
            "enabled": True,
            "schedule": {
                "preview_seconds": 0.2,
                "pause_on_fullscreen": True,
                "reflow_on_idle": True,
                "idle_threshold_seconds": 5,
                "resume_grace_seconds": 1,
                "quiet_hours": {"enabled": False},
                "reminders": [
                    {
                        "id": "micro",
                        "kind": "micro_break",
                        "label": "微休息",
                        "interval_minutes": 10,
                        "duration_seconds": 3,
                        "preview_seconds": 0.2,
                        "snooze_minutes": 0.5,
                    },
                    {
                        "id": "pomodoro",
                        "kind": "pomodoro",
                        "label": "25/5角色番茄钟",
                        "work_minutes": 25,
                        "break_minutes": 5,
                        "long_break_minutes": 15,
                        "focus_sessions_per_long_break": 4,
                        "duration_seconds": 300,
                        "preview_seconds": 0.2,
                        "snooze_minutes": 0.5,
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
                "minute_seconds": 0.1,
                "preview_seconds": 0.2,
                "duration_seconds": 3,
            },
        }

    def active_manifest_path(self) -> Path:
        return self.manifest_path

    def companion_pet_id(self) -> str:
        return "fake-pet"

    def present_system_reminder(self, action, text) -> bool:
        self.presented.append((action, text))
        return True


def test_controller_runs_health_flow_in_same_window_and_menu(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ONEPIC_BREAK_TEST_MODE", "1")
    app = QApplication.instance() or QApplication([])
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    pet = FakePet(manifest)
    pet.show()
    clock = Clock()
    controller = BreakReminderController(
        pet,
        store=BreakReminderStore(tmp_path / "settings.json"),
        clock=clock,
        monitor_system_state=False,
    )
    controller.tick_timer.stop()
    controller.status_timer.stop()

    menu = QMenu()
    controller.populate_menu(menu)
    assert "休息提醒 / 角色番茄钟" in [action.text() for action in menu.actions()]
    assert controller.sound_enabled is False
    assert controller.statistics_enabled is False

    clock.now = 0.8
    controller.tick()
    assert pet.presented[-1][0] == "curious"
    clock.now = 1.0
    controller.tick()
    app.processEvents()
    assert controller.panel.isVisible() is True
    assert controller.panel.complete_button.text() == "完成"
    assert controller.panel.snooze_button.text() == "稍后"
    assert controller.panel.skip_button.text() == "跳过"

    controller.snooze_current()
    assert controller.panel.isVisible() is False
    assert controller.engine.active_reminder_id is None
    assert controller.status_text().startswith("下一项")

    controller.shutdown()
    menu.close()
    pet.close()
    app.processEvents()


def test_controller_reuses_onepic_pomodoro_state_for_25_5_cycle(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ONEPIC_BREAK_TEST_MODE", "1")
    app = QApplication.instance() or QApplication([])
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    pet = FakePet(manifest)
    pet.show()
    clock = Clock()
    controller = BreakReminderController(
        pet,
        store=BreakReminderStore(tmp_path / "settings.json"),
        clock=clock,
        monitor_system_state=False,
    )
    controller.tick_timer.stop()
    controller.status_timer.stop()

    controller.restart_pomodoro("pomodoro")
    assert isinstance(controller.pomodoro, PomodoroState)
    assert controller.pomodoro.phase is PomodoroPhase.FOCUS
    assert controller.pomodoro.running is True

    clock.now = 2.5
    controller.tick()
    app.processEvents()
    assert controller.pomodoro.phase is PomodoroPhase.SHORT_BREAK
    assert controller.panel.isVisible() is True

    controller.complete_current()
    assert controller.pomodoro.phase is PomodoroPhase.FOCUS
    assert controller.pomodoro.running is True
    assert controller.panel.isVisible() is False

    controller.shutdown()
    pet.close()
    app.processEvents()


def test_system_reminder_visual_does_not_reset_user_inactivity() -> None:
    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings())
    marker = window._last_user_interaction

    assert window.present_system_reminder("happy", "该休息一下啦。") is True
    assert window.state is PetState.HAPPY
    assert window._last_user_interaction == marker

    window.close()
    window.deleteLater()
    app.processEvents()
