"""
本模块把休息提醒调度器接入普通桌宠的现有角色窗口和菜单。

职责范围：
- 创建贴近角色、可点击完成/稍后/跳过的轻量提醒卡；
- 把预告、到点、完成、延期、跳过、庆祝和重排映射到当前角色已有动作与气泡；
- 在同一系统托盘和桌宠右键菜单中提供启用、暂停、手动触发、声音、统计与开机启动；
- 定时读取不含内容的空闲时长和前台窗口几何，调用纯调度器；
- 在切换角色形象后重新读取该角色清单，不创建第二个应用或第二个托盘。

所有数据留在本机。声音、本地统计和开机启动均默认关闭并由用户主动开启；
模块不访问网络、不读取窗口标题、不采集按键文本，也不要求管理员权限。
"""

from __future__ import annotations

import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .break_scheduler import (
    BreakReminderConfig,
    BreakReminderStore,
    SchedulerEngine,
    SchedulerEvent,
    is_fullscreen_foreground,
    normalize_break_reminder_config,
    seconds_since_input,
    set_startup,
    startup_enabled,
)
from .companion.tools import (
    PomodoroConfig,
    PomodoroPhase,
    PomodoroState,
    PomodoroTransition,
)


class ReminderPanel(QWidget):
    """显示在角色旁边的透明提醒卡，按钮均为真实 Qt 控件。"""

    complete_requested = Signal()
    snooze_requested = Signal()
    skip_requested = Signal()
    expired = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._deadline: float | None = None
        self.setObjectName("onepicBreakReminderPanel")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setMinimumWidth(292)
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(7)
        self.title_label = QLabel("休息提醒")
        self.title_label.setObjectName("title")
        self.message_label = QLabel("")
        self.message_label.setObjectName("message")
        self.message_label.setWordWrap(True)
        self.message_label.setMaximumWidth(300)
        self.countdown_label = QLabel("")
        self.countdown_label.setObjectName("countdown")
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)
        layout.addWidget(self.message_label)
        layout.addWidget(self.countdown_label)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self.complete_button = QPushButton("完成")
        self.snooze_button = QPushButton("稍后")
        self.skip_button = QPushButton("跳过")
        for button in (
            self.complete_button,
            self.snooze_button,
            self.skip_button,
        ):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        self.complete_button.clicked.connect(self.complete_requested)
        self.snooze_button.clicked.connect(self.snooze_requested)
        self.skip_button.clicked.connect(self.skip_requested)
        layout.addLayout(actions)
        outer.addWidget(self.card)

        self.setStyleSheet(
            "QFrame#card {"
            "background-color: rgba(246, 255, 251, 244);"
            "border: 2px solid #28a98b;"
            "border-radius: 17px;"
            "}"
            "QLabel#title { color: #167c68; font: 700 15px 'Microsoft YaHei UI'; }"
            "QLabel#message { color: #173f37; font: 13px 'Microsoft YaHei UI'; }"
            "QLabel#countdown { color: #173f37; font: 700 14px 'Segoe UI'; }"
            "QPushButton {"
            "background: #ffffff; color: #176f5e; border: 1px solid #28a98b;"
            "border-radius: 10px; padding: 5px 11px; font: 12px 'Microsoft YaHei UI';"
            "}"
            "QPushButton:hover { background: #28a98b; color: white; }"
        )
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(200)
        self.countdown_timer.timeout.connect(self._update_countdown)

    def show_reminder(
        self,
        *,
        title: str,
        message: str,
        duration_seconds: float,
    ) -> None:
        """更新提醒内容并启动建议休息时长倒计时。"""

        self.title_label.setText(title)
        self.message_label.setText(message)
        self._deadline = time.monotonic() + max(1.0, duration_seconds)
        self._update_countdown()
        self.countdown_timer.start()
        self.adjustSize()
        self.show()
        self.raise_()

    def stop(self) -> None:
        """停止倒计时并隐藏卡片。"""

        self._deadline = None
        self.countdown_timer.stop()
        self.hide()

    @property
    def is_counting(self) -> bool:
        """返回卡片是否仍持有一项有效提醒倒计时。"""

        return self._deadline is not None

    def _update_countdown(self) -> None:
        if self._deadline is None:
            self.countdown_label.clear()
            return
        remaining = max(0, int(self._deadline - time.monotonic() + 0.999))
        minutes, seconds = divmod(remaining, 60)
        self.countdown_label.setText(f"建议休息 {minutes:02d}:{seconds:02d}")
        if remaining <= 0:
            self._deadline = None
            self.countdown_timer.stop()
            self.expired.emit()

    def anchor_to(self, pet_window: QWidget) -> None:
        """把卡片放在角色上方，空间不足时改放左右侧并限制在当前屏幕。"""

        screen = QApplication.screenAt(pet_window.frameGeometry().center())
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        gap = 10
        bubble = getattr(pet_window, "dialogue_bubble", None)
        bubble_visible = bool(
            bubble is not None
            and callable(getattr(bubble, "isVisible", None))
            and bubble.isVisible()
        )
        if bubble_visible:
            x = bubble.x() + (bubble.width() - self.width()) // 2
            y = bubble.y() - self.height() - gap
        else:
            x = pet_window.x() + (pet_window.width() - self.width()) // 2
            y = pet_window.y() - self.height() - gap
        if y < area.top():
            x = pet_window.x() + pet_window.width() + gap
            y = pet_window.y() + (pet_window.height() - self.height()) // 2
            if x + self.width() > area.right() + 1:
                x = pet_window.x() - self.width() - gap
        x = min(max(x, area.left()), area.right() - self.width() + 1)
        y = min(max(y, area.top()), area.bottom() - self.height() + 1)
        self.move(QPoint(x, y))


class BreakReminderController:
    """协调当前角色清单、调度器、提醒卡和两个现有菜单入口。"""

    def __init__(
        self,
        pet_window: QWidget,
        *,
        store: BreakReminderStore | None = None,
        clock: Callable[[], float] = time.monotonic,
        monitor_system_state: bool = True,
    ) -> None:
        self.window = pet_window
        self.store = store or BreakReminderStore()
        self.clock = clock
        self.monitor_system_state = monitor_system_state
        self.panel = ReminderPanel()
        self.panel.complete_requested.connect(self.complete_current)
        self.panel.snooze_requested.connect(self.snooze_current)
        self.panel.skip_requested.connect(self.skip_current)
        self.panel.expired.connect(self.complete_current)
        self._status_actions: list[QAction] = []
        self._pause_actions: list[QAction] = []
        self._snooze_actions: list[QAction] = []
        self._skip_actions: list[QAction] = []
        self._enabled_actions: list[QAction] = []
        self._sound_actions: list[QAction] = []
        self._stats_actions: list[QAction] = []
        self._startup_actions: list[QAction] = []
        self._manifest_path: Path | None = None
        self._raw_config: dict[str, Any] = {}
        self.pet_id = "demo"
        self.config = normalize_break_reminder_config({})
        self.engine = SchedulerEngine(())
        self.enabled = False
        self.sound_enabled = False
        self.statistics_enabled = False
        self.available = False
        self.pomodoro_definition = None
        self.pomodoro: PomodoroState | None = None
        self._pomodoro_started = False
        self._pomodoro_blocked = False
        self._pomodoro_snooze_deadline: float | None = None
        self._reload_from_window()

        self.tick_timer = QTimer()
        self.tick_timer.setInterval(500)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start()
        self.status_timer = QTimer()
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self.update_actions)
        self.status_timer.start()

    def _window_value(self, name: str, default: object = None) -> object:
        callback = getattr(self.window, name, None)
        return callback() if callable(callback) else default

    def _reload_from_window(self) -> None:
        """读取当前角色清单，并应用该角色单独保存的用户开关。"""

        self.panel.stop()
        raw = self._window_value("break_reminder_config", {})
        self._raw_config = dict(raw) if isinstance(raw, dict) else {}
        manifest = self._window_value("active_manifest_path")
        self._manifest_path = Path(manifest) if manifest else None
        pet_id = self._window_value("companion_pet_id", "demo")
        self.pet_id = str(pet_id).strip() or "demo"
        test_mode = os.environ.get("ONEPIC_BREAK_TEST_MODE") == "1"
        self.config = normalize_break_reminder_config(
            self._raw_config,
            test_mode=test_mode,
        )
        self.available = bool(self._raw_config)
        health_definitions = tuple(
            item for item in self.config.definitions if item.kind != "pomodoro"
        )
        self.engine = SchedulerEngine(
            health_definitions,
            now=self.clock(),
            quiet_hours=self.config.quiet_hours,
            idle_threshold_seconds=self.config.idle_threshold_seconds,
            resume_grace_seconds=self.config.resume_grace_seconds,
            clock=self.clock,
        )
        self.pomodoro_definition = next(
            (
                item
                for item in self.config.definitions
                if item.kind == "pomodoro"
            ),
            None,
        )
        self.pomodoro = None
        self._pomodoro_started = False
        self._pomodoro_blocked = False
        self._pomodoro_snooze_deadline = None
        if self.pomodoro_definition is not None:
            definition = self.pomodoro_definition
            self.pomodoro = PomodoroState(
                PomodoroConfig(
                    focus_seconds=definition.work_seconds or 25 * 60,
                    short_break_seconds=definition.break_seconds or 5 * 60,
                    long_break_seconds=definition.long_break_seconds or 15 * 60,
                    focus_sessions_per_long_break=(
                        definition.focus_sessions_per_long_break
                    ),
                )
            )
        self.enabled = self.store.setting(
            self.pet_id,
            "enabled",
            self.config.enabled,
        )
        self.sound_enabled = self.store.setting(
            self.pet_id,
            "sound_enabled",
            self.config.sounds_enabled_default,
        )
        self.statistics_enabled = self.store.setting(
            self.pet_id,
            "statistics_enabled",
            self.config.statistics_enabled_default,
        )
        if self.store.setting(self.pet_id, "paused", False):
            self.engine.pause(self.clock())
        elif (
            self.enabled
            and self.pomodoro is not None
            and self.pomodoro_definition is not None
            and self.pomodoro_definition.auto_start
        ):
            self.pomodoro.start(self.clock())
            self._pomodoro_started = True
        self.update_actions()

    def on_pet_changed(self) -> None:
        """角色切换后由 PetWindow 调用，避免把一个角色的计划串到另一个角色。"""

        self._reload_from_window()

    def _line(self, stage: str, label: str = "休息提醒") -> str:
        lines = self.config.lines_for(stage)
        text = random.choice(lines) if lines else label
        try:
            return text.format(label=label)
        except (KeyError, ValueError):
            return text

    def _show_character_state(
        self,
        stage: str,
        *,
        label: str,
        text: str | None = None,
    ) -> None:
        """通过现有白名单桥接动作与纯文本气泡，不解释配置中的代码。"""

        show = getattr(self.window, "show", None)
        if callable(show):
            show()
        action = self.config.state_mapping.get(stage)
        present = getattr(self.window, "present_system_reminder", None)
        if callable(present):
            present(action, text or self._line(stage, label))
            return
        show_text = getattr(self.window, "show_companion_text", None)
        if callable(show_text):
            show_text(text or self._line(stage, label))

    def tick(self) -> None:
        """采样一次非内容系统状态并推进计划。"""

        window_visible = bool(getattr(self.window, "isVisible", lambda: True)())
        if not window_visible:
            self.panel.hide()
        elif self.panel.is_counting:
            if not self.panel.isVisible():
                self.panel.show()
            self.panel.anchor_to(self.window)
        if not self.available or not self.enabled:
            return
        idle = 0.0
        fullscreen = False
        if self.monitor_system_state:
            if self.config.reflow_on_idle:
                idle = seconds_since_input()
            if self.config.pause_on_fullscreen:
                fullscreen = is_fullscreen_foreground()
        now = self.clock()
        events = self.engine.tick(
            now,
            idle_seconds=idle,
            fullscreen=fullscreen,
            wall_now=datetime.now(),
        )
        for event in events:
            self.handle_event(event)
        self._tick_pomodoro(now)

    def _tick_pomodoro(self, now: float) -> None:
        """用 OnePic 既有 PomodoroState 推进唯一的角色番茄阶段。"""

        if self.pomodoro is None or not self.enabled:
            return
        blocked = bool(self.engine.blocked_reason)
        if blocked:
            if self.pomodoro.running:
                self.pomodoro.pause(now)
                self._pomodoro_blocked = True
            if self._pomodoro_snooze_deadline is not None:
                self._pomodoro_snooze_deadline = now + max(
                    self.config.resume_grace_seconds,
                    1.0,
                )
            self.panel.stop()
            return
        if self._pomodoro_blocked:
            self._pomodoro_blocked = False
            self.pomodoro.start(now)
        if self._pomodoro_snooze_deadline is not None:
            if now < self._pomodoro_snooze_deadline:
                return
            self._pomodoro_snooze_deadline = None
            self.pomodoro.start(now)
            self._show_pomodoro_break()
            return
        if not self.pomodoro.running:
            return
        transition = self.pomodoro.sync(now, auto_start_next=True)
        if transition is not None:
            self._handle_pomodoro_transition(transition)

    def _pomodoro_break_active(self) -> bool:
        return bool(
            self.pomodoro is not None
            and self.pomodoro.phase
            in {PomodoroPhase.SHORT_BREAK, PomodoroPhase.LONG_BREAK}
            and (
                self.pomodoro.running
                or self._pomodoro_snooze_deadline is not None
            )
        )

    def _pomodoro_phase_label(self) -> str:
        if self.pomodoro is None:
            return "角色番茄钟"
        if self.pomodoro.phase is PomodoroPhase.LONG_BREAK:
            return "番茄钟长休息"
        if self.pomodoro.phase is PomodoroPhase.SHORT_BREAK:
            return "番茄钟短休息"
        return "番茄专注"

    def _show_pomodoro_break(self) -> None:
        """在短休息或长休息阶段显示同一张持久操作卡。"""

        if self.pomodoro is None:
            return
        label = self._pomodoro_phase_label()
        message = self._line("active", label)
        self._show_character_state("active", label=label, text=message)
        remaining = self.pomodoro.remaining(self.clock())
        self.panel.show_reminder(
            title=label,
            message=message,
            duration_seconds=max(1.0, remaining),
        )
        self.panel.anchor_to(self.window)
        self._increment("pomodoro_break")

    def _handle_pomodoro_transition(
        self,
        transition: PomodoroTransition,
    ) -> None:
        """把已有番茄模型的阶段切换转换成角色提醒。"""

        if transition.previous_phase is PomodoroPhase.FOCUS:
            # 番茄阶段切换优先于同刻到期的健康提醒；后者按自身延期配置
            # 重新排队，避免两张卡或连续弹窗。
            if self.engine.active_reminder_id:
                self.engine.snooze(self.clock())
            self._show_pomodoro_break()
        else:
            self.panel.stop()
            self._increment("pomodoro_focus")
            self._show_character_state(
                "complete",
                label="番茄钟休息",
                text="休息结束，新一轮专注开始。",
            )
        self.update_actions()

    def handle_event(self, event: SchedulerEvent) -> None:
        """把纯语义事件转换成角色动作、气泡、提醒卡和可选声音。"""

        if event.stage == "preview":
            self._show_character_state("preview", label=event.label)
            self._increment("preview")
        elif event.stage == "active":
            message = self._line("active", event.label)
            self._show_character_state(
                "active",
                label=event.label,
                text=message,
            )
            self.panel.show_reminder(
                title=event.label,
                message=message,
                duration_seconds=event.duration_seconds,
            )
            self.panel.anchor_to(self.window)
            self._increment(event.kind)
            self._play_sound(event)
        elif event.stage == "deferred":
            self.panel.stop()
        elif event.stage == "reflow":
            self._show_character_state("reflow", label=event.label)
        self.update_actions()

    def complete_current(self) -> None:
        """完成当前提醒；倒计时自然结束也走相同路径。"""

        event = self.engine.complete(self.clock())
        if event is None and self._pomodoro_break_active():
            assert self.pomodoro is not None
            transition = self.pomodoro.complete_phase(
                start_next_at=self.clock(),
            )
            self._pomodoro_snooze_deadline = None
            self.panel.stop()
            self._increment("complete")
            self._handle_pomodoro_transition(transition)
            return
        if event is None:
            return
        self.panel.stop()
        self._increment("complete")
        self._show_character_state("complete", label=event.label)

        def celebrate() -> None:
            self._show_character_state("celebrate", label=event.label)

        QTimer.singleShot(1100, celebrate)
        self.update_actions()

    def snooze_current(self) -> None:
        """按该项目配置延期当前提醒。"""

        event = self.engine.snooze(self.clock())
        if event is None and self._pomodoro_break_active():
            assert self.pomodoro is not None
            now = self.clock()
            if self.pomodoro.running:
                self.pomodoro.pause(now)
            delay = (
                self.pomodoro_definition.snooze_seconds
                if self.pomodoro_definition is not None
                else 120.0
            )
            self._pomodoro_snooze_deadline = now + delay
            self.panel.stop()
            self._increment("snooze")
            self._show_character_state(
                "snooze",
                label=self._pomodoro_phase_label(),
            )
            self.update_actions()
            return
        if event is None:
            return
        self.panel.stop()
        self._increment("snooze")
        self._show_character_state("snooze", label=event.label)
        self.update_actions()

    def skip_current(self) -> None:
        """跳过当前提醒但保留下一周期。"""

        event = self.engine.skip(self.clock())
        if event is None and self._pomodoro_break_active():
            assert self.pomodoro is not None
            label = self._pomodoro_phase_label()
            self.pomodoro.complete_phase(start_next_at=self.clock())
            self._pomodoro_snooze_deadline = None
            self.panel.stop()
            self._increment("skip")
            self._show_character_state("skip", label=label)
            self.update_actions()
            return
        if event is None:
            return
        self.panel.stop()
        self._increment("skip")
        self._show_character_state("skip", label=event.label)
        self.update_actions()

    def show_manual_preview(self) -> None:
        """只预览下一项的角色表现，不改变真实截止时间。"""

        next_item = self.engine.next_item(self.clock())
        label = (
            next_item[0].label
            if next_item
            else (
                self.pomodoro_definition.label
                if self.pomodoro_definition is not None
                else "休息提醒"
            )
        )
        self._show_character_state("preview", label=label)

    def trigger_reminder(self, reminder_id: str) -> None:
        """立即显示一项提醒，供用户主动调用和加速 QA。"""

        event = self.engine.trigger(reminder_id, self.clock())
        self.handle_event(event)

    def restart_pomodoro(self, reminder_id: str) -> None:
        """从当前时刻开始或重启 25/5 等清单定义的番茄周期。"""

        definition = self.pomodoro_definition
        if (
            definition is None
            or definition.id != reminder_id
            or self.pomodoro is None
        ):
            return
        if not self.enabled:
            self.enabled = True
            self.store.set_setting(self.pet_id, "enabled", True)
            self.engine.reset_schedule(self.clock())
        self.pomodoro.reset_all()
        self.pomodoro.start(self.clock())
        self._pomodoro_started = True
        self._pomodoro_snooze_deadline = None
        self.panel.stop()
        minutes = max(1, round((definition.work_seconds or 1500) / 60))
        if self.config.test_mode:
            message = f"{definition.label}已在加速测试模式重启。"
        else:
            message = f"{definition.label}开始，专注 {minutes} 分钟后提醒休息。"
        self._show_character_state(
            "preview",
            label=definition.label,
            text=message,
        )
        self.update_actions()

    def toggle_enabled(self, enabled: bool) -> None:
        """启用或关闭当前角色的主动提醒。"""

        self.enabled = bool(enabled)
        self.store.set_setting(self.pet_id, "enabled", self.enabled)
        if not self.enabled:
            if self.pomodoro is not None and self.pomodoro.running:
                self.pomodoro.pause(self.clock())
            self.panel.stop()
        else:
            self.engine.reset_schedule(self.clock())
            if (
                self.pomodoro is not None
                and self._pomodoro_started
                and not self.pomodoro.running
            ):
                self.pomodoro.start(self.clock())
            self._show_character_state(
                "resumed",
                label="休息提醒",
                text="角色休息提醒已启用。",
            )
        self.update_actions()

    def toggle_pause(self) -> None:
        """单独暂停或恢复提醒，不改变桌宠跑动状态。"""

        if self.engine.paused:
            self.engine.resume(self.clock())
            if (
                self.pomodoro is not None
                and not self.pomodoro.running
                and self._pomodoro_started
            ):
                self.pomodoro.start(self.clock())
            self.store.set_setting(self.pet_id, "paused", False)
            self._show_character_state("resumed", label="休息提醒")
        else:
            if self.engine.active_reminder_id:
                self.engine.snooze(self.clock())
            if self.pomodoro is not None and self.pomodoro.running:
                self.pomodoro.pause(self.clock())
            self.panel.stop()
            self.engine.pause(self.clock())
            self.store.set_setting(self.pet_id, "paused", True)
            self._show_character_state("paused", label="休息提醒")
        self.update_actions()

    def set_sound_enabled(self, enabled: bool) -> None:
        self.sound_enabled = bool(enabled)
        self.store.set_setting(self.pet_id, "sound_enabled", self.sound_enabled)
        self.update_actions()

    def set_statistics_enabled(self, enabled: bool) -> None:
        self.statistics_enabled = bool(enabled)
        self.store.set_setting(
            self.pet_id,
            "statistics_enabled",
            self.statistics_enabled,
        )
        self.update_actions()

    def set_startup_enabled(self, enabled: bool) -> None:
        """切换当前用户启动项；不改变系统级设置。"""

        set_startup(bool(enabled))
        self.update_actions()

    def _increment(self, name: str) -> None:
        self.store.increment(
            self.pet_id,
            name,
            enabled=self.statistics_enabled,
        )

    def _play_sound(self, event: SchedulerEvent) -> None:
        if not self.sound_enabled or self._manifest_path is None:
            return
        relative = (
            self.config.sound_files.get(event.kind)
            or self.config.sound_files.get(event.stage)
            or self.config.sound_files.get("reminder")
        )
        if not relative:
            return
        root = self._manifest_path.parent.resolve()
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            return
        if sys.platform == "win32" and path.suffix.lower() == ".wav":
            import winsound

            winsound.PlaySound(
                str(path),
                winsound.SND_FILENAME
                | winsound.SND_ASYNC
                | winsound.SND_NODEFAULT,
            )

    def open_data_directory(self) -> None:
        """打开仅包含本模块设置和可选计数的目录。"""

        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(self.store.path.parent)  # type: ignore[attr-defined]

    def status_text(self) -> str:
        """生成托盘可读状态，不包含用户内容或窗口标题。"""

        if not self.available:
            return "当前形象未配置休息提醒"
        if not self.enabled:
            return "休息提醒已关闭"
        if self.engine.paused:
            return "休息提醒已暂停"
        if self.engine.blocked_reason:
            labels = {
                "fullscreen": "全屏暂停中",
                "idle": "离开后重排中",
                "quiet_hours": "勿扰时段",
            }
            return labels.get(self.engine.blocked_reason, "提醒暂缓中")
        if self.engine.active_reminder_id:
            definition = self.engine.reminders[
                self.engine.active_reminder_id
            ].definition
            return f"正在提醒：{definition.label}"
        candidates: list[tuple[str, float]] = []
        next_item = self.engine.next_item(self.clock())
        if next_item is not None:
            definition, seconds = next_item
            candidates.append((definition.label, seconds))
        if self.pomodoro is not None and self.pomodoro.running:
            candidates.append(
                (
                    self._pomodoro_phase_label(),
                    self.pomodoro.remaining(self.clock()),
                )
            )
        if not candidates:
            return "没有启用的提醒项目"
        label, seconds = min(candidates, key=lambda item: item[1])
        minutes, rest = divmod(max(0, int(seconds)), 60)
        return f"下一项：{label} · {minutes:02d}:{rest:02d}"

    @staticmethod
    def _sync_action_list(
        actions: list[QAction],
        callback: Callable[[QAction], None],
    ) -> None:
        alive: list[QAction] = []
        for action in actions:
            try:
                callback(action)
                alive.append(action)
            except RuntimeError:
                continue
        actions[:] = alive

    def update_actions(self) -> None:
        """同步长驻托盘动作；已销毁的右键菜单动作会被安全移除。"""

        active = bool(self.engine.active_reminder_id) or self._pomodoro_break_active()
        status = self.status_text()
        self._sync_action_list(
            self._status_actions,
            lambda action: action.setText(status),
        )
        self._sync_action_list(
            self._pause_actions,
            lambda action: (
                action.setText("恢复提醒" if self.engine.paused else "暂停提醒"),
                action.setEnabled(self.available and self.enabled),
            ),
        )
        self._sync_action_list(
            self._snooze_actions,
            lambda action: action.setEnabled(active),
        )
        self._sync_action_list(
            self._skip_actions,
            lambda action: action.setEnabled(active),
        )
        self._sync_action_list(
            self._enabled_actions,
            lambda action: (
                action.setChecked(self.enabled),
                action.setEnabled(self.available),
            ),
        )
        self._sync_action_list(
            self._sound_actions,
            lambda action: action.setChecked(self.sound_enabled),
        )
        self._sync_action_list(
            self._stats_actions,
            lambda action: action.setChecked(self.statistics_enabled),
        )
        self._sync_action_list(
            self._startup_actions,
            lambda action: action.setChecked(startup_enabled()),
        )

    def populate_menu(self, menu: QMenu) -> None:
        """把完整提醒菜单加入现有托盘或桌宠右键菜单。"""

        reminder_menu = menu.addMenu("休息提醒 / 角色番茄钟")
        status_action = QAction(self.status_text(), reminder_menu)
        status_action.setEnabled(False)
        reminder_menu.addAction(status_action)
        self._status_actions.append(status_action)
        reminder_menu.addSeparator()

        enabled_action = QAction("启用主动提醒", reminder_menu)
        enabled_action.setCheckable(True)
        enabled_action.setChecked(self.enabled)
        enabled_action.setEnabled(self.available)
        enabled_action.toggled.connect(self.toggle_enabled)
        reminder_menu.addAction(enabled_action)
        self._enabled_actions.append(enabled_action)

        preview_action = QAction("预览下一项角色表现", reminder_menu)
        preview_action.setEnabled(self.available)
        preview_action.triggered.connect(self.show_manual_preview)
        reminder_menu.addAction(preview_action)

        trigger_menu = reminder_menu.addMenu("立即提醒（测试 / 主动调用）")
        health_definitions = [
            item for item in self.config.definitions if item.kind != "pomodoro"
        ]
        trigger_menu.setEnabled(bool(health_definitions))
        for definition in health_definitions:
            action = QAction(definition.label, trigger_menu)
            action.triggered.connect(
                lambda _checked=False, reminder_id=definition.id: (
                    self.trigger_reminder(reminder_id)
                )
            )
            trigger_menu.addAction(action)

        pomodoros = [
            item for item in self.config.definitions if item.kind == "pomodoro"
        ]
        if pomodoros:
            pomodoro_menu = reminder_menu.addMenu("开始 / 重启角色番茄钟")
            for definition in pomodoros:
                action = QAction(definition.label, pomodoro_menu)
                action.triggered.connect(
                    lambda _checked=False, reminder_id=definition.id: (
                        self.restart_pomodoro(reminder_id)
                    )
                )
                pomodoro_menu.addAction(action)

        pause_action = QAction(
            "恢复提醒" if self.engine.paused else "暂停提醒",
            reminder_menu,
        )
        pause_action.triggered.connect(self.toggle_pause)
        reminder_menu.addAction(pause_action)
        self._pause_actions.append(pause_action)
        snooze_action = QAction("当前提醒稍后再说", reminder_menu)
        snooze_action.triggered.connect(self.snooze_current)
        reminder_menu.addAction(snooze_action)
        self._snooze_actions.append(snooze_action)
        skip_action = QAction("跳过当前提醒", reminder_menu)
        skip_action.triggered.connect(self.skip_current)
        reminder_menu.addAction(skip_action)
        self._skip_actions.append(skip_action)

        reminder_menu.addSeparator()
        sound_action = QAction("声音（默认关闭）", reminder_menu)
        sound_action.setCheckable(True)
        sound_action.setChecked(self.sound_enabled)
        sound_action.toggled.connect(self.set_sound_enabled)
        reminder_menu.addAction(sound_action)
        self._sound_actions.append(sound_action)
        stats_action = QAction("匿名本地计数（默认关闭）", reminder_menu)
        stats_action.setCheckable(True)
        stats_action.setChecked(self.statistics_enabled)
        stats_action.toggled.connect(self.set_statistics_enabled)
        reminder_menu.addAction(stats_action)
        self._stats_actions.append(stats_action)
        startup_action = QAction("开机启动（当前用户）", reminder_menu)
        startup_action.setCheckable(True)
        startup_action.setChecked(startup_enabled())
        startup_action.toggled.connect(self.set_startup_enabled)
        reminder_menu.addAction(startup_action)
        self._startup_actions.append(startup_action)
        data_action = QAction("打开提醒本地数据目录", reminder_menu)
        data_action.triggered.connect(self.open_data_directory)
        reminder_menu.addAction(data_action)
        self.update_actions()

    def shutdown(self) -> None:
        """停止两个本地计时器并关闭提醒卡，不改变用户设置。"""

        self.tick_timer.stop()
        self.status_timer.stop()
        self.panel.stop()
        self.panel.close()


__all__ = ["BreakReminderController", "ReminderPanel"]
