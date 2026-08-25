"""
本模块实现普通桌宠可选的休息提醒、喝水、久坐和番茄钟调度核心。

职责范围：
- 校验并归一化运行清单中的 ``break_reminders`` 配置；
- 用可注入的单调时钟生成预告、到点、延期、跳过、完成与重排事件；
- 在勿扰时段、全屏窗口或用户长时间离开时顺延提醒；
- 只通过 Win32 几何和最后输入时间判断系统状态，不读取窗口标题或输入内容；
- 把用户开关和可选统计原子写入当前用户本地目录；
- 用 HKCU 的当前用户启动项提供无需管理员权限的可选开机启动。

本模块不依赖 Qt，不访问网络，也不加载角色素材。窗口、托盘和声音由
``break_reminder_ui.py`` 负责，因此纯调度逻辑可以在加速时钟下确定性测试。
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time as wall_time
from pathlib import Path
from typing import Any, Callable, Mapping


SUPPORTED_KINDS = frozenset(
    {"micro_break", "long_break", "pomodoro", "water", "sedentary", "custom"}
)
SAFE_ACTIONS = frozenset(
    {"wave", "happy", "shy", "surprised", "annoyed", "sleepy", "curious"}
)
DEFAULT_STATE_MAPPING = {
    "preview": "curious",
    "active": "wave",
    "complete": "happy",
    "snooze": "curious",
    "skip": "curious",
    "celebrate": "happy",
    "reflow": "curious",
}
REMINDER_PRIORITY = {
    "long_break": 10,
    "sedentary": 20,
    "water": 30,
    "micro_break": 40,
    "custom": 50,
    "pomodoro": 0,
}
DEFAULT_DIALOGUE = {
    "preview": ("{label}快到时间啦，先把手上的事情收个尾吧。",),
    "active": ("{label}时间到。完成、稍后或跳过都由你决定。",),
    "complete": ("做得好，{label}完成啦。",),
    "snooze": ("好，我稍后再来提醒你。",),
    "skip": ("这次先跳过，我会继续按计划陪你。",),
    "celebrate": ("休息也是认真生活的一部分。",),
    "reflow": ("检测到勿扰、全屏或离开状态，提醒已重新安排。",),
    "paused": ("休息提醒已暂停，可从托盘随时恢复。",),
    "resumed": ("休息提醒已恢复。",),
}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class BreakReminderConfigError(ValueError):
    """表示桌宠清单中的休息提醒配置不合法。"""


@dataclass(frozen=True)
class ReminderDefinition:
    """描述一项可重复调度的提醒。"""

    id: str
    kind: str
    label: str
    interval_seconds: float
    duration_seconds: float
    preview_seconds: float
    snooze_seconds: float
    work_seconds: float | None = None
    break_seconds: float | None = None
    long_break_seconds: float | None = None
    focus_sessions_per_long_break: int = 4
    auto_start: bool = False


@dataclass
class ReminderRuntime:
    """保存单项提醒在当前应用会话中的可变状态。"""

    definition: ReminderDefinition
    next_due: float
    preview_sent: bool = False
    active: bool = False
    occurrences: int = 0


@dataclass(frozen=True)
class SchedulerEvent:
    """描述调度器交给桌宠界面的一次语义事件。"""

    reminder_id: str
    kind: str
    label: str
    stage: str
    duration_seconds: float
    occurred_at: float
    reason: str = ""


@dataclass(frozen=True)
class BreakReminderConfig:
    """保存已校验、可直接供运行时使用的休息提醒配置。"""

    enabled: bool
    definitions: tuple[ReminderDefinition, ...]
    quiet_hours: Mapping[str, Any]
    idle_threshold_seconds: float
    resume_grace_seconds: float
    pause_on_fullscreen: bool
    reflow_on_idle: bool
    state_mapping: Mapping[str, str]
    dialogue: Mapping[str, tuple[str, ...]]
    sounds_enabled_default: bool
    sound_files: Mapping[str, str]
    statistics_enabled_default: bool
    test_mode: bool

    def lines_for(self, stage: str) -> tuple[str, ...]:
        """返回指定语义状态的台词池。"""

        return self.dialogue.get(stage, DEFAULT_DIALOGUE.get(stage, ()))


def _finite_number(
    value: object,
    name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """把输入转换成有限且位于范围内的浮点数。"""

    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BreakReminderConfigError(f"{name} 必须是数字") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise BreakReminderConfigError(
            f"{name} 必须位于 {minimum:g}–{maximum:g}"
        )
    return result


def _normalize_dialogue(value: object) -> dict[str, tuple[str, ...]]:
    """清理状态台词，拒绝脚本结构并限制单句和池大小。"""

    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise BreakReminderConfigError("break_reminders.dialogue 必须是对象")
    normalized = dict(DEFAULT_DIALOGUE)
    for raw_stage, raw_lines in value.items():
        stage = str(raw_stage).strip()
        if stage not in DEFAULT_DIALOGUE:
            continue
        if not isinstance(raw_lines, list):
            raise BreakReminderConfigError(f"dialogue.{stage} 必须是字符串数组")
        lines: list[str] = []
        for raw_line in raw_lines[:24]:
            line = str(raw_line).strip()
            if line and line not in lines:
                lines.append(line[:120])
        if not lines:
            raise BreakReminderConfigError(f"dialogue.{stage} 至少需要一句台词")
        normalized[stage] = tuple(lines)
    return normalized


def _normalize_state_mapping(value: object) -> dict[str, str]:
    """把语义状态限制到桌宠现有的安全动作白名单。"""

    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise BreakReminderConfigError("break_reminders.state_mapping 必须是对象")
    normalized = dict(DEFAULT_STATE_MAPPING)
    for raw_stage, raw_action in value.items():
        stage = str(raw_stage).strip()
        if stage not in DEFAULT_STATE_MAPPING:
            continue
        action = str(raw_action).strip().lower()
        if action not in SAFE_ACTIONS:
            raise BreakReminderConfigError(
                f"state_mapping.{stage} 只能使用现有安全动作"
            )
        normalized[stage] = action
    return normalized


def normalize_break_reminder_config(
    value: object,
    *,
    test_mode: bool = False,
) -> BreakReminderConfig:
    """校验一段 ``break_reminders`` 清单并生成运行配置。"""

    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise BreakReminderConfigError("break_reminders 必须是 JSON 对象")
    if value and int(value.get("schema_version", 1)) != 1:
        raise BreakReminderConfigError("break_reminders.schema_version 必须为 1")

    enabled = bool(value.get("enabled", False))
    raw_test = value.get("test_mode", {}) if test_mode else {}
    if not isinstance(raw_test, dict):
        raise BreakReminderConfigError("break_reminders.test_mode 必须是对象")
    minute_seconds = _finite_number(
        raw_test.get("minute_seconds", 60),
        "test_mode.minute_seconds",
        minimum=0.05,
        maximum=60,
    )
    duration_override = raw_test.get("duration_seconds")
    preview_override = raw_test.get("preview_seconds")

    raw_schedule = value.get("schedule", {})
    if not isinstance(raw_schedule, dict):
        raise BreakReminderConfigError("break_reminders.schedule 必须是对象")
    default_preview = _finite_number(
        raw_schedule.get("preview_seconds", 10),
        "schedule.preview_seconds",
        minimum=0,
        maximum=3600,
    )
    raw_reminders = raw_schedule.get("reminders", [])
    if not isinstance(raw_reminders, list):
        raise BreakReminderConfigError("schedule.reminders 必须是数组")
    if enabled and not raw_reminders:
        raise BreakReminderConfigError("启用休息提醒时 schedule.reminders 不能为空")

    definitions: list[ReminderDefinition] = []
    ids: set[str] = set()
    for index, raw_item in enumerate(raw_reminders):
        prefix = f"schedule.reminders[{index}]"
        if not isinstance(raw_item, dict):
            raise BreakReminderConfigError(f"{prefix} 必须是对象")
        if not bool(raw_item.get("enabled", True)):
            continue
        reminder_id = str(raw_item.get("id", "")).strip()
        if not _ID_PATTERN.fullmatch(reminder_id):
            raise BreakReminderConfigError(f"{prefix}.id 格式不合法")
        if reminder_id in ids:
            raise BreakReminderConfigError(f"提醒 id 重复：{reminder_id}")
        ids.add(reminder_id)
        kind = str(raw_item.get("kind", "")).strip()
        if kind not in SUPPORTED_KINDS:
            raise BreakReminderConfigError(f"{prefix}.kind 不受支持")
        label = str(raw_item.get("label", "")).strip()[:40]
        if not label:
            raise BreakReminderConfigError(f"{prefix}.label 不能为空")

        if kind == "pomodoro":
            work_minutes = _finite_number(
                raw_item.get("work_minutes", 25),
                f"{prefix}.work_minutes",
                minimum=0.1,
                maximum=1440,
            )
            break_minutes = _finite_number(
                raw_item.get("break_minutes", 5),
                f"{prefix}.break_minutes",
                minimum=0.1,
                maximum=240,
            )
            long_break_minutes = _finite_number(
                raw_item.get("long_break_minutes", 15),
                f"{prefix}.long_break_minutes",
                minimum=0.1,
                maximum=480,
            )
            raw_sessions = raw_item.get("focus_sessions_per_long_break", 4)
            if (
                isinstance(raw_sessions, bool)
                or not isinstance(raw_sessions, int)
                or not 1 <= raw_sessions <= 12
            ):
                raise BreakReminderConfigError(
                    f"{prefix}.focus_sessions_per_long_break 必须是 1–12 的整数"
                )
            work_seconds = work_minutes * minute_seconds
            break_seconds = break_minutes * minute_seconds
            long_break_seconds = long_break_minutes * minute_seconds
            interval_seconds = work_seconds
        else:
            interval_minutes = _finite_number(
                raw_item.get("interval_minutes", 0),
                f"{prefix}.interval_minutes",
                minimum=0.1,
                maximum=1440,
            )
            interval_seconds = interval_minutes * minute_seconds
            work_seconds = None
            break_seconds = None
            long_break_seconds = None
            raw_sessions = 4
        duration_seconds = _finite_number(
            (
                duration_override
                if duration_override is not None
                else raw_item.get("duration_seconds", 20)
            ),
            f"{prefix}.duration_seconds",
            minimum=1,
            maximum=3600,
        )
        if break_seconds is not None:
            duration_seconds = break_seconds
        preview_seconds = _finite_number(
            (
                preview_override
                if preview_override is not None
                else raw_item.get("preview_seconds", default_preview)
            ),
            f"{prefix}.preview_seconds",
            minimum=0,
            maximum=3600,
        )
        preview_seconds = min(preview_seconds, interval_seconds)
        snooze_minutes = _finite_number(
            raw_item.get("snooze_minutes", 2),
            f"{prefix}.snooze_minutes",
            minimum=0.1,
            maximum=240,
        )
        definitions.append(
            ReminderDefinition(
                id=reminder_id,
                kind=kind,
                label=label,
                interval_seconds=interval_seconds,
                duration_seconds=duration_seconds,
                preview_seconds=preview_seconds,
                snooze_seconds=snooze_minutes * minute_seconds,
                work_seconds=work_seconds,
                break_seconds=break_seconds,
                long_break_seconds=long_break_seconds,
                focus_sessions_per_long_break=raw_sessions,
                auto_start=bool(raw_item.get("auto_start", False)),
            )
        )

    quiet_hours = raw_schedule.get("quiet_hours", {"enabled": False})
    if not isinstance(quiet_hours, dict):
        raise BreakReminderConfigError("schedule.quiet_hours 必须是对象")
    quiet_start = str(quiet_hours.get("start", "22:30"))
    quiet_end = str(quiet_hours.get("end", "08:00"))
    try:
        wall_time.fromisoformat(quiet_start)
        wall_time.fromisoformat(quiet_end)
    except ValueError as exc:
        raise BreakReminderConfigError("勿扰时段必须使用 HH:MM 格式") from exc
    normalized_quiet = {
        "enabled": bool(quiet_hours.get("enabled", False)),
        "start": quiet_start,
        "end": quiet_end,
    }

    privacy = value.get("privacy", {})
    if not isinstance(privacy, dict):
        raise BreakReminderConfigError("break_reminders.privacy 必须是对象")
    if privacy and privacy.get("network_default", "off") != "off":
        raise BreakReminderConfigError("休息提醒默认不得联网")
    if privacy and privacy.get("collect_window_titles", False) is not False:
        raise BreakReminderConfigError("休息提醒不得收集窗口标题")
    statistics_default = bool(
        privacy.get("statistics_enabled_default", False)
    )

    sounds = value.get("sounds", {})
    if not isinstance(sounds, dict):
        raise BreakReminderConfigError("break_reminders.sounds 必须是对象")
    raw_sound_files = sounds.get("files", {})
    if not isinstance(raw_sound_files, dict):
        raise BreakReminderConfigError("break_reminders.sounds.files 必须是对象")
    sound_files: dict[str, str] = {}
    for raw_name, raw_relative in raw_sound_files.items():
        name = str(raw_name).strip()
        relative = str(raw_relative).strip().replace("\\", "/")
        path = Path(relative)
        if (
            not name
            or not relative
            or path.is_absolute()
            or ".." in path.parts
            or path.suffix.lower() != ".wav"
        ):
            raise BreakReminderConfigError(f"声音路径不安全：{raw_name}")
        sound_files[name] = relative

    return BreakReminderConfig(
        enabled=enabled,
        definitions=tuple(definitions),
        quiet_hours=normalized_quiet,
        idle_threshold_seconds=_finite_number(
            raw_schedule.get("idle_threshold_seconds", 300),
            "schedule.idle_threshold_seconds",
            minimum=0,
            maximum=86400,
        ),
        resume_grace_seconds=_finite_number(
            raw_schedule.get("resume_grace_seconds", 5),
            "schedule.resume_grace_seconds",
            minimum=0.1,
            maximum=3600,
        ),
        pause_on_fullscreen=bool(raw_schedule.get("pause_on_fullscreen", True)),
        reflow_on_idle=bool(raw_schedule.get("reflow_on_idle", True)),
        state_mapping=_normalize_state_mapping(value.get("state_mapping")),
        dialogue=_normalize_dialogue(value.get("dialogue")),
        sounds_enabled_default=bool(sounds.get("enabled_default", False)),
        sound_files=sound_files,
        statistics_enabled_default=statistics_default,
        test_mode=bool(test_mode),
    )


def canonical_break_reminder_manifest(value: object) -> dict[str, Any]:
    """把已校验配置收敛为可安全写入角色运行清单的字段集合。"""

    config = normalize_break_reminder_config(value, test_mode=False)
    reminders: list[dict[str, Any]] = []
    for definition in config.definitions:
        item: dict[str, Any] = {
            "id": definition.id,
            "kind": definition.kind,
            "label": definition.label,
            "enabled": True,
            "duration_seconds": definition.duration_seconds,
            "preview_seconds": definition.preview_seconds,
            "snooze_minutes": definition.snooze_seconds / 60,
        }
        if definition.kind == "pomodoro":
            item.update(
                {
                    "auto_start": definition.auto_start,
                    "work_minutes": (definition.work_seconds or 1500) / 60,
                    "break_minutes": (definition.break_seconds or 300) / 60,
                    "long_break_minutes": (
                        definition.long_break_seconds or 900
                    )
                    / 60,
                    "focus_sessions_per_long_break": (
                        definition.focus_sessions_per_long_break
                    ),
                }
            )
        else:
            item["interval_minutes"] = definition.interval_seconds / 60
        reminders.append(item)

    raw = value if isinstance(value, dict) else {}
    raw_test = raw.get("test_mode", {})
    test_mode: dict[str, float] = {}
    if isinstance(raw_test, dict) and raw_test:
        test_mode = {
            "minute_seconds": _finite_number(
                raw_test.get("minute_seconds", 60),
                "test_mode.minute_seconds",
                minimum=0.05,
                maximum=60,
            ),
            "preview_seconds": _finite_number(
                raw_test.get("preview_seconds", 1),
                "test_mode.preview_seconds",
                minimum=0,
                maximum=3600,
            ),
            "duration_seconds": _finite_number(
                raw_test.get("duration_seconds", 3),
                "test_mode.duration_seconds",
                minimum=1,
                maximum=3600,
            ),
        }
    result: dict[str, Any] = {
        "schema_version": 1,
        "enabled": config.enabled,
        "schedule": {
            "preview_seconds": min(
                (item.preview_seconds for item in config.definitions),
                default=10,
            ),
            "pause_on_fullscreen": config.pause_on_fullscreen,
            "reflow_on_idle": config.reflow_on_idle,
            "idle_threshold_seconds": config.idle_threshold_seconds,
            "resume_grace_seconds": config.resume_grace_seconds,
            "quiet_hours": dict(config.quiet_hours),
            "reminders": reminders,
        },
        "state_mapping": dict(config.state_mapping),
        "dialogue": {
            stage: list(lines)
            for stage, lines in config.dialogue.items()
        },
        "sounds": {
            "enabled_default": config.sounds_enabled_default,
            "files": dict(config.sound_files),
        },
        "privacy": {
            "network_default": "off",
            "collect_window_titles": False,
            "statistics_enabled_default": config.statistics_enabled_default,
        },
    }
    if test_mode:
        result["test_mode"] = test_mode
    return result


def in_quiet_hours(
    start: str,
    end: str,
    now: datetime | None = None,
) -> bool:
    """判断本地时间是否位于可跨午夜的勿扰区间。"""

    now = now or datetime.now()
    start_time = wall_time.fromisoformat(start)
    end_time = wall_time.fromisoformat(end)
    current = now.time().replace(tzinfo=None)
    if start_time == end_time:
        return False
    if start_time < end_time:
        return start_time <= current < end_time
    return current >= start_time or current < end_time


class SchedulerEngine:
    """维护提醒计划，并对暂停、阻塞和用户操作做确定性转换。"""

    def __init__(
        self,
        definitions: tuple[ReminderDefinition, ...] | list[ReminderDefinition],
        *,
        now: float | None = None,
        quiet_hours: Mapping[str, Any] | None = None,
        idle_threshold_seconds: float = 300,
        resume_grace_seconds: float = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.clock = clock
        started_at = clock() if now is None else float(now)
        self.reminders = {
            item.id: ReminderRuntime(item, started_at + item.interval_seconds)
            for item in definitions
        }
        self.quiet_hours = dict(quiet_hours or {"enabled": False})
        self.idle_threshold_seconds = max(0.0, float(idle_threshold_seconds))
        self.resume_grace_seconds = max(0.1, float(resume_grace_seconds))
        self.paused = False
        self.pause_started: float | None = None
        self.blocked_reason: str | None = None
        self.active_reminder_id: str | None = None
        self.history: deque[SchedulerEvent] = deque(maxlen=512)
        self.last_tick_at = started_at

    def blocking_reason(
        self,
        *,
        idle_seconds: float,
        fullscreen: bool,
        wall_now: datetime | None,
    ) -> str | None:
        """返回当前阻塞原因，优先全屏，其次离开和勿扰时段。"""

        if fullscreen:
            return "fullscreen"
        if self.idle_threshold_seconds and idle_seconds >= self.idle_threshold_seconds:
            return "idle"
        if self.quiet_hours.get("enabled") and in_quiet_hours(
            str(self.quiet_hours.get("start", "22:30")),
            str(self.quiet_hours.get("end", "08:00")),
            wall_now,
        ):
            return "quiet_hours"
        return None

    def _record(self, event: SchedulerEvent) -> SchedulerEvent:
        self.history.append(event)
        return event

    def _defer_active(self, now: float, reason: str) -> SchedulerEvent | None:
        """全屏或离开时撤下已显示提醒，并在恢复后重新预告。"""

        if self.active_reminder_id is None:
            return None
        runtime = self.reminders[self.active_reminder_id]
        runtime.active = False
        runtime.preview_sent = False
        runtime.next_due = now + max(
            self.resume_grace_seconds,
            runtime.definition.preview_seconds,
        )
        self.active_reminder_id = None
        return self._record(
            SchedulerEvent(
                runtime.definition.id,
                runtime.definition.kind,
                runtime.definition.label,
                "deferred",
                0,
                now,
                reason,
            )
        )

    def tick(
        self,
        now: float | None = None,
        *,
        idle_seconds: float = 0,
        fullscreen: bool = False,
        wall_now: datetime | None = None,
    ) -> list[SchedulerEvent]:
        """推进一次调度，返回本轮新产生的事件。"""

        timestamp = self.clock() if now is None else float(now)
        if self.paused:
            self.last_tick_at = timestamp
            return []
        long_tick_gap = timestamp - self.last_tick_at > max(
            60.0,
            self.idle_threshold_seconds,
        )
        self.last_tick_at = timestamp
        reason = self.blocking_reason(
            idle_seconds=idle_seconds,
            fullscreen=fullscreen,
            wall_now=wall_now,
        )
        if reason:
            self.blocked_reason = reason
            deferred = self._defer_active(timestamp, reason)
            return [deferred] if deferred is not None else []
        if self.blocked_reason:
            previous = self.blocked_reason
            self.blocked_reason = None
            self._reflow_overdue(timestamp)
            return [
                self._record(
                    SchedulerEvent(
                        "system",
                        "system",
                        "提醒已重排",
                        "reflow",
                        0,
                        timestamp,
                        previous,
                    )
                )
            ]
        if long_tick_gap and not self.active_reminder_id:
            self._reflow_overdue(timestamp)
            return [
                self._record(
                    SchedulerEvent(
                        "system",
                        "system",
                        "提醒已重排",
                        "reflow",
                        0,
                        timestamp,
                        "long_tick_gap",
                    )
                )
            ]
        if self.active_reminder_id:
            return []

        events: list[SchedulerEvent] = []
        for runtime in sorted(
            self.reminders.values(),
            key=lambda item: (
                item.next_due,
                REMINDER_PRIORITY.get(item.definition.kind, 99),
            ),
        ):
            definition = runtime.definition
            if (
                not runtime.preview_sent
                and timestamp >= runtime.next_due - definition.preview_seconds
            ):
                runtime.preview_sent = True
                events.append(
                    self._record(
                        SchedulerEvent(
                            definition.id,
                            definition.kind,
                            definition.label,
                            "preview",
                            definition.preview_seconds,
                            timestamp,
                        )
                    )
                )
            if timestamp >= runtime.next_due:
                runtime.active = True
                runtime.occurrences += 1
                self.active_reminder_id = definition.id
                events.append(
                    self._record(
                        SchedulerEvent(
                            definition.id,
                            definition.kind,
                            definition.label,
                            "active",
                            definition.duration_seconds,
                            timestamp,
                        )
                    )
                )
                break
        return events

    def _reflow_overdue(self, now: float) -> None:
        for runtime in self.reminders.values():
            if runtime.next_due <= now:
                runtime.next_due = now + max(
                    self.resume_grace_seconds,
                    runtime.definition.preview_seconds,
                )
                runtime.preview_sent = False

    def pause(self, now: float | None = None) -> None:
        """暂停未来调度；重复调用不改变计划。"""

        if self.paused:
            return
        self.paused = True
        self.pause_started = self.clock() if now is None else float(now)

    def resume(self, now: float | None = None) -> None:
        """恢复调度并把所有截止时间整体后移暂停时长。"""

        if not self.paused:
            return
        timestamp = self.clock() if now is None else float(now)
        started_at = self.pause_started
        paused_for = max(
            0.0,
            timestamp - (timestamp if started_at is None else started_at),
        )
        for runtime in self.reminders.values():
            runtime.next_due += paused_for
        self.paused = False
        self.pause_started = None

    def _finish_active(
        self,
        now: float,
        *,
        action: str,
        delay: float | None = None,
    ) -> SchedulerEvent | None:
        if not self.active_reminder_id:
            return None
        runtime = self.reminders[self.active_reminder_id]
        definition = runtime.definition
        runtime.active = False
        runtime.preview_sent = False
        self.active_reminder_id = None
        runtime.next_due = now + (
            delay
            if delay is not None
            else definition.work_seconds or definition.interval_seconds
        )
        for other in self.reminders.values():
            if other is runtime:
                continue
            if other.next_due <= now:
                other.next_due = now + max(
                    self.resume_grace_seconds,
                    other.definition.preview_seconds,
                )
                other.preview_sent = False
        return self._record(
            SchedulerEvent(
                definition.id,
                definition.kind,
                definition.label,
                action,
                0,
                now,
            )
        )

    def complete(self, now: float | None = None) -> SchedulerEvent | None:
        """完成当前提醒并开始下一周期。"""

        timestamp = self.clock() if now is None else float(now)
        return self._finish_active(timestamp, action="complete")

    def snooze(self, now: float | None = None) -> SchedulerEvent | None:
        """按当前项目配置的延期时长推迟提醒。"""

        timestamp = self.clock() if now is None else float(now)
        if not self.active_reminder_id:
            return None
        delay = self.reminders[
            self.active_reminder_id
        ].definition.snooze_seconds
        return self._finish_active(
            timestamp,
            action="snooze",
            delay=delay,
        )

    def skip(self, now: float | None = None) -> SchedulerEvent | None:
        """跳过本次提醒并开始下一周期。"""

        timestamp = self.clock() if now is None else float(now)
        return self._finish_active(timestamp, action="skip")

    def trigger(
        self,
        reminder_id: str,
        now: float | None = None,
    ) -> SchedulerEvent:
        """手动触发一项已配置提醒，供菜单预览和 QA 使用。"""

        timestamp = self.clock() if now is None else float(now)
        if reminder_id not in self.reminders:
            raise KeyError(reminder_id)
        if self.active_reminder_id:
            self.skip(timestamp)
        runtime = self.reminders[reminder_id]
        runtime.next_due = timestamp
        runtime.preview_sent = True
        runtime.active = True
        runtime.occurrences += 1
        self.active_reminder_id = reminder_id
        definition = runtime.definition
        return self._record(
            SchedulerEvent(
                definition.id,
                definition.kind,
                definition.label,
                "active",
                definition.duration_seconds,
                timestamp,
                "manual-trigger",
            )
        )

    def restart_cycle(
        self,
        reminder_id: str,
        now: float | None = None,
    ) -> ReminderDefinition:
        """从当前时刻重新开始某项周期，适合手动启动番茄专注。"""

        timestamp = self.clock() if now is None else float(now)
        if reminder_id not in self.reminders:
            raise KeyError(reminder_id)
        runtime = self.reminders[reminder_id]
        if self.active_reminder_id == reminder_id:
            self.active_reminder_id = None
        runtime.active = False
        runtime.preview_sent = False
        runtime.next_due = timestamp + runtime.definition.interval_seconds
        return runtime.definition

    def reset_schedule(self, now: float | None = None) -> None:
        """从当前时刻重新计算全部项目，避免关闭很久后启用时集中补发。"""

        timestamp = self.clock() if now is None else float(now)
        self.active_reminder_id = None
        self.blocked_reason = None
        for runtime in self.reminders.values():
            runtime.next_due = timestamp + runtime.definition.interval_seconds
            runtime.preview_sent = False
            runtime.active = False

    def next_item(
        self,
        now: float | None = None,
    ) -> tuple[ReminderDefinition, float] | None:
        """返回离到点最近的项目及剩余秒数。"""

        timestamp = self.clock() if now is None else float(now)
        if not self.reminders:
            return None
        runtime = min(self.reminders.values(), key=lambda item: item.next_due)
        return runtime.definition, max(0.0, runtime.next_due - timestamp)

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        """返回不含用户内容的结构化运行快照。"""

        timestamp = self.clock() if now is None else float(now)
        return {
            "paused": self.paused,
            "blocked_reason": self.blocked_reason,
            "active_reminder_id": self.active_reminder_id,
            "reminders": {
                reminder_id: {
                    "next_in_seconds": max(0.0, runtime.next_due - timestamp),
                    "preview_sent": runtime.preview_sent,
                    "active": runtime.active,
                    "occurrences": runtime.occurrences,
                }
                for reminder_id, runtime in self.reminders.items()
            },
        }


def seconds_since_input() -> float:
    """返回 Windows 最后一次用户输入距今秒数，不读取输入内容。"""

    if sys.platform != "win32":
        return 0.0

    class LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    tick = ctypes.windll.kernel32.GetTickCount()
    return max(0.0, ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0)


def is_fullscreen_foreground() -> bool:
    """仅比较窗口与显示器几何，判断前台窗口是否覆盖整个屏幕。"""

    if sys.platform != "win32":
        return False

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("rcMonitor", Rect),
            ("rcWork", Rect),
            ("dwFlags", ctypes.c_ulong),
        ]

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd or hwnd == user32.GetShellWindow():
        return False
    rect = Rect()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    monitor = user32.MonitorFromWindow(hwnd, 2)
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    target = info.rcMonitor
    tolerance = 3
    return (
        abs(rect.left - target.left) <= tolerance
        and abs(rect.top - target.top) <= tolerance
        and abs(rect.right - target.right) <= tolerance
        and abs(rect.bottom - target.bottom) <= tolerance
    )


def _data_root() -> Path:
    """返回休息提醒的当前用户本地数据目录。"""

    override = os.environ.get("ONEPIC_SETTINGS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(
        os.environ.get(
            "LOCALAPPDATA",
            Path.home() / "AppData" / "Local",
        )
    )
    return base / "OnePicDesktopPet"


class BreakReminderStore:
    """保存按宠物隔离的开关和可关闭统计，不记录用户行为内容。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (_data_root() / "break-reminders.json")
        self.data: dict[str, Any] = {
            "schema_version": 1,
            "profiles": {},
        }
        self.load_error: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.load_error = type(exc).__name__
            return
        if isinstance(value, dict) and isinstance(value.get("profiles", {}), dict):
            self.data = {
                "schema_version": 1,
                "profiles": dict(value.get("profiles", {})),
            }

    def profile(self, pet_id: str) -> dict[str, Any]:
        """返回指定宠物的本地设置副本。"""

        raw = self.data["profiles"].get(pet_id, {})
        return dict(raw) if isinstance(raw, dict) else {}

    def setting(self, pet_id: str, name: str, default: bool) -> bool:
        """读取一个布尔用户开关。"""

        return bool(self.profile(pet_id).get(name, default))

    def set_setting(self, pet_id: str, name: str, value: bool) -> None:
        """更新一个布尔开关并原子保存。"""

        profiles = self.data["profiles"]
        profile = profiles.setdefault(pet_id, {})
        if not isinstance(profile, dict):
            profile = {}
            profiles[pet_id] = profile
        profile[name] = bool(value)
        self.save()

    def increment(self, pet_id: str, name: str, *, enabled: bool) -> None:
        """只在用户启用统计时增加匿名本地计数。"""

        if not enabled:
            return
        profiles = self.data["profiles"]
        profile = profiles.setdefault(pet_id, {})
        if not isinstance(profile, dict):
            profile = {}
            profiles[pet_id] = profile
        statistics = profile.setdefault("statistics", {})
        if not isinstance(statistics, dict):
            statistics = {}
            profile["statistics"] = statistics
        statistics[name] = int(statistics.get(name, 0)) + 1
        self.save()

    def save(self) -> None:
        """使用同目录临时文件替换，避免退出中断产生半截 JSON。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix="break-reminders-",
            suffix=".json.tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


STARTUP_VALUE_NAME = "OnePicDesktopPet"
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_enabled() -> bool:
    """返回当前用户是否已经启用 OnePic 开机启动。"""

    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
        return str(value).strip() == startup_command().strip()
    except OSError:
        return False


def startup_command() -> str:
    """生成当前 EXE 或源码入口的安全 Windows 命令行。"""

    if getattr(sys, "frozen", False):
        arguments = [str(Path(sys.executable).resolve())]
    else:
        launcher = Path(__file__).resolve().parents[2] / "main.py"
        arguments = [str(Path(sys.executable).resolve()), str(launcher)]
    return subprocess.list2cmdline(arguments)


def set_startup(enabled: bool, command: str | None = None) -> None:
    """仅写 HKCU 当前用户启动项，不请求管理员权限。"""

    if sys.platform != "win32":
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
        if enabled:
            winreg.SetValueEx(
                key,
                STARTUP_VALUE_NAME,
                0,
                winreg.REG_SZ,
                command or startup_command(),
            )
        else:
            try:
                winreg.DeleteValue(key, STARTUP_VALUE_NAME)
            except FileNotFoundError:
                pass


__all__ = [
    "BreakReminderConfig",
    "BreakReminderConfigError",
    "BreakReminderStore",
    "ReminderDefinition",
    "SchedulerEngine",
    "SchedulerEvent",
    "in_quiet_hours",
    "is_fullscreen_foreground",
    "normalize_break_reminder_config",
    "canonical_break_reminder_manifest",
    "seconds_since_input",
    "set_startup",
    "startup_command",
    "startup_enabled",
]
