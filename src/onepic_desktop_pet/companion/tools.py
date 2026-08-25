"""
本模块提供与 Qt、文件系统和网络解耦的桌宠本地工具状态模型。

职责范围：
- 用显式单调时钟值维护正计时与倒计时，方便窗口层稳定刷新；
- 管理专注、短休息和长休息组成的番茄阶段循环；
- 在容许的轮询延迟内生成一次且仅一次的整点报时事件；
- 生成包含上下界的范围随机整数，并允许测试或调用方注入随机源。

模块不会主动读取系统时间、创建线程或弹出窗口。调用方负责传入
``time.monotonic()`` 或 ``datetime``，因此所有状态转换都可确定性测试。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Iterable, Protocol


class IntegerRandomSource(Protocol):
    """描述范围随机数所需的最小随机源接口。"""

    def randint(self, minimum: int, maximum: int, /) -> int:
        """返回包含上下界的随机整数。"""


def _finite_number(value: float, name: str) -> float:
    """把输入转换为有限浮点数。"""

    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} 必须是数字") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{name} 必须是有限数字")
    return normalized


def _duration(value: float, name: str, *, allow_zero: bool = True) -> float:
    """校验非负或正数时长。"""

    normalized = _finite_number(value, name)
    minimum_is_valid = normalized >= 0 if allow_zero else normalized > 0
    if not minimum_is_valid:
        qualifier = "非负" if allow_zero else "大于零"
        raise ValueError(f"{name} 必须{qualifier}")
    return normalized


def _elapsed_since(now: float, started_at: float) -> float:
    """计算单调时钟差，并拒绝倒退的时钟值。"""

    current = _finite_number(now, "now")
    if current < started_at:
        raise ValueError("now 不能早于计时启动时刻")
    return current - started_at


@dataclass(frozen=True)
class TimerSnapshot:
    """提供给窗口层的一次不可变计时快照。"""

    running: bool
    elapsed_seconds: float
    remaining_seconds: float | None
    finished: bool


class CountUpTimer:
    """维护可暂停、继续和复位的正计时状态。"""

    def __init__(self) -> None:
        self._accumulated_seconds = 0.0
        self._started_at: float | None = None

    @property
    def running(self) -> bool:
        """返回当前是否正在计时。"""

        return self._started_at is not None

    @property
    def accumulated_seconds(self) -> float:
        """返回上次暂停前已经固化的秒数。"""

        return self._accumulated_seconds

    def start(self, now: float) -> bool:
        """从给定单调时刻启动或继续；已经运行时返回 ``False``。"""

        timestamp = _finite_number(now, "now")
        if self.running:
            return False
        self._started_at = timestamp
        return True

    def pause(self, now: float) -> bool:
        """在给定时刻暂停并固化经过时间。"""

        if self._started_at is None:
            return False
        self._accumulated_seconds += _elapsed_since(now, self._started_at)
        self._started_at = None
        return True

    def reset(self) -> None:
        """清零并回到暂停状态。"""

        self._accumulated_seconds = 0.0
        self._started_at = None

    def elapsed(self, now: float | None = None) -> float:
        """返回当前累计秒数；运行中必须提供当前单调时刻。"""

        if self._started_at is None:
            return self._accumulated_seconds
        if now is None:
            raise ValueError("运行中的正计时需要提供 now")
        return self._accumulated_seconds + _elapsed_since(now, self._started_at)

    def snapshot(self, now: float | None = None) -> TimerSnapshot:
        """返回适合一次界面刷新的稳定快照。"""

        return TimerSnapshot(
            running=self.running,
            elapsed_seconds=self.elapsed(now),
            remaining_seconds=None,
            finished=False,
        )


class CountdownTimer:
    """维护以固定总时长为起点的倒计时状态。"""

    def __init__(self, duration_seconds: float) -> None:
        self._duration_seconds = _duration(duration_seconds, "duration_seconds")
        self._remaining_seconds = self._duration_seconds
        self._started_at: float | None = None

    @property
    def duration_seconds(self) -> float:
        """返回本轮倒计时总时长。"""

        return self._duration_seconds

    @property
    def running(self) -> bool:
        """返回内部状态是否仍在运行。"""

        return self._started_at is not None

    @property
    def finished(self) -> bool:
        """返回已经同步并停在零秒的完成状态。"""

        return self._remaining_seconds <= 0 and not self.running

    def start(self, now: float) -> bool:
        """启动或继续倒计时；零秒或已经运行时不改变状态。"""

        timestamp = _finite_number(now, "now")
        if self.running or self._remaining_seconds <= 0:
            return False
        self._started_at = timestamp
        return True

    def remaining(self, now: float | None = None) -> float:
        """返回尚余秒数，且不改变内部状态。"""

        if self._started_at is None:
            return self._remaining_seconds
        if now is None:
            raise ValueError("运行中的倒计时需要提供 now")
        return max(
            0.0,
            self._remaining_seconds - _elapsed_since(now, self._started_at),
        )

    def elapsed(self, now: float | None = None) -> float:
        """返回本轮已经经过的秒数。"""

        return self._duration_seconds - self.remaining(now)

    def is_finished(self, now: float | None = None) -> bool:
        """查询给定时刻是否到零，而不触发状态转换。"""

        return self.remaining(now) <= 0

    def sync(self, now: float) -> bool:
        """把运行结果固化；仅首次从运行转为完成时返回 ``True``。"""

        if self._started_at is None:
            return False
        remaining = self.remaining(now)
        if remaining > 0:
            return False
        self._remaining_seconds = 0.0
        self._started_at = None
        return True

    def pause(self, now: float) -> bool:
        """在给定时刻暂停，已到零时同时固化完成状态。"""

        if self._started_at is None:
            return False
        self._remaining_seconds = self.remaining(now)
        self._started_at = None
        return True

    def reset(self, duration_seconds: float | None = None) -> None:
        """复位；可同时替换后续使用的总时长。"""

        if duration_seconds is not None:
            self._duration_seconds = _duration(
                duration_seconds,
                "duration_seconds",
            )
        self._remaining_seconds = self._duration_seconds
        self._started_at = None

    def snapshot(self, now: float | None = None) -> TimerSnapshot:
        """返回适合一次界面刷新的稳定快照。"""

        remaining = self.remaining(now)
        finished = remaining <= 0
        return TimerSnapshot(
            running=self.running and not finished,
            elapsed_seconds=self._duration_seconds - remaining,
            remaining_seconds=remaining,
            finished=finished,
        )


class PomodoroPhase(StrEnum):
    """番茄钟支持的三个阶段。"""

    FOCUS = "focus"
    SHORT_BREAK = "short_break"
    LONG_BREAK = "long_break"


@dataclass(frozen=True)
class PomodoroConfig:
    """定义番茄阶段时长和长休息频率。"""

    focus_seconds: float = 25 * 60
    short_break_seconds: float = 5 * 60
    long_break_seconds: float = 15 * 60
    focus_sessions_per_long_break: int = 4

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "focus_seconds",
            _duration(self.focus_seconds, "focus_seconds", allow_zero=False),
        )
        object.__setattr__(
            self,
            "short_break_seconds",
            _duration(
                self.short_break_seconds,
                "short_break_seconds",
                allow_zero=False,
            ),
        )
        object.__setattr__(
            self,
            "long_break_seconds",
            _duration(
                self.long_break_seconds,
                "long_break_seconds",
                allow_zero=False,
            ),
        )
        if (
            isinstance(self.focus_sessions_per_long_break, bool)
            or not isinstance(self.focus_sessions_per_long_break, int)
            or self.focus_sessions_per_long_break <= 0
        ):
            raise ValueError("focus_sessions_per_long_break 必须是正整数")

    def duration_for(self, phase: PomodoroPhase) -> float:
        """返回指定阶段的秒数。"""

        if phase is PomodoroPhase.FOCUS:
            return self.focus_seconds
        if phase is PomodoroPhase.SHORT_BREAK:
            return self.short_break_seconds
        if phase is PomodoroPhase.LONG_BREAK:
            return self.long_break_seconds
        raise ValueError(f"未知番茄阶段：{phase!r}")


@dataclass(frozen=True)
class PomodoroTransition:
    """描述一次番茄阶段切换。"""

    previous_phase: PomodoroPhase
    next_phase: PomodoroPhase
    completed_focus_sessions: int


@dataclass(frozen=True)
class PomodoroSnapshot:
    """汇总番茄阶段和内部倒计时快照。"""

    phase: PomodoroPhase
    timer: TimerSnapshot
    completed_focus_sessions: int
    focus_sessions_until_long_break: int


class PomodoroState:
    """维护番茄阶段序列以及当前阶段的倒计时。"""

    def __init__(self, config: PomodoroConfig | None = None) -> None:
        self.config = config or PomodoroConfig()
        self.phase = PomodoroPhase.FOCUS
        self.completed_focus_sessions = 0
        self.countdown = CountdownTimer(self.current_duration_seconds)

    @property
    def current_duration_seconds(self) -> float:
        """返回当前阶段配置的总秒数。"""

        return self.config.duration_for(self.phase)

    @property
    def running(self) -> bool:
        """返回当前阶段是否正在倒计时。"""

        return self.countdown.running

    @property
    def focus_sessions_until_long_break(self) -> int:
        """返回距离下一次长休息还需完成的专注轮数。"""

        interval = self.config.focus_sessions_per_long_break
        remainder = self.completed_focus_sessions % interval
        return interval if remainder == 0 else interval - remainder

    def start(self, now: float) -> bool:
        """启动或继续当前阶段。"""

        return self.countdown.start(now)

    def pause(self, now: float) -> bool:
        """暂停当前阶段。"""

        return self.countdown.pause(now)

    def remaining(self, now: float | None = None) -> float:
        """返回当前阶段尚余秒数。"""

        return self.countdown.remaining(now)

    def reset_current(self) -> None:
        """复位当前阶段，但保留已完成专注轮数。"""

        self.countdown.reset(self.current_duration_seconds)

    def reset_all(self) -> None:
        """回到第一轮专注并清除阶段计数。"""

        self.phase = PomodoroPhase.FOCUS
        self.completed_focus_sessions = 0
        self.countdown.reset(self.current_duration_seconds)

    def complete_phase(
        self,
        *,
        start_next_at: float | None = None,
    ) -> PomodoroTransition:
        """结束当前阶段，切到规则指定的下一阶段。"""

        previous = self.phase
        if previous is PomodoroPhase.FOCUS:
            self.completed_focus_sessions += 1
            interval = self.config.focus_sessions_per_long_break
            if self.completed_focus_sessions % interval == 0:
                self.phase = PomodoroPhase.LONG_BREAK
            else:
                self.phase = PomodoroPhase.SHORT_BREAK
        else:
            self.phase = PomodoroPhase.FOCUS

        self.countdown.reset(self.current_duration_seconds)
        if start_next_at is not None:
            self.countdown.start(start_next_at)
        return PomodoroTransition(
            previous_phase=previous,
            next_phase=self.phase,
            completed_focus_sessions=self.completed_focus_sessions,
        )

    def sync(
        self,
        now: float,
        *,
        auto_start_next: bool = False,
    ) -> PomodoroTransition | None:
        """当前阶段到零时只切换一次，可选自动启动下一阶段。"""

        if not self.countdown.is_finished(now):
            return None
        self.countdown.sync(now)
        return self.complete_phase(
            start_next_at=now if auto_start_next else None,
        )

    def snapshot(self, now: float | None = None) -> PomodoroSnapshot:
        """返回当前番茄周期的不可变快照。"""

        return PomodoroSnapshot(
            phase=self.phase,
            timer=self.countdown.snapshot(now),
            completed_focus_sessions=self.completed_focus_sessions,
            focus_sessions_until_long_break=(
                self.focus_sessions_until_long_break
            ),
        )


@dataclass(frozen=True)
class HourlyChimeEvent:
    """描述一次应由上层展示或朗读的整点事件。"""

    scheduled_at: datetime
    hour: int
    announcement: str


class HourlyChimeScheduler:
    """通过轮询生成带容错窗口且同一整点不重复的报时事件。"""

    def __init__(
        self,
        *,
        grace_seconds: float = 90.0,
        enabled_hours: Iterable[int] | None = None,
    ) -> None:
        grace = _duration(grace_seconds, "grace_seconds")
        if grace >= 3600:
            raise ValueError("grace_seconds 必须小于一小时")
        self.grace_seconds = grace

        hours = range(24) if enabled_hours is None else enabled_hours
        normalized: set[int] = set()
        for hour in hours:
            if isinstance(hour, bool) or not isinstance(hour, int):
                raise TypeError("enabled_hours 只能包含整数小时")
            if not 0 <= hour <= 23:
                raise ValueError("enabled_hours 的小时必须位于 0 到 23")
            normalized.add(hour)
        if not normalized:
            raise ValueError("enabled_hours 至少需要一个小时")
        self.enabled_hours = frozenset(normalized)
        self._emitted_keys: set[tuple[object, ...]] = set()

    @staticmethod
    def _key(scheduled_at: datetime) -> tuple[object, ...]:
        """为日期、小时、时区和 DST 折叠状态生成去重键。"""

        offset = scheduled_at.utcoffset()
        offset_seconds = None if offset is None else offset.total_seconds()
        return (
            scheduled_at.date(),
            scheduled_at.hour,
            scheduled_at.fold,
            offset_seconds,
        )

    def poll(self, now: datetime) -> HourlyChimeEvent | None:
        """轮询当前时间，返回尚未发出的整点事件或 ``None``。"""

        if not isinstance(now, datetime):
            raise TypeError("now 必须是 datetime")
        scheduled_at = now.replace(minute=0, second=0, microsecond=0)
        delay = (now - scheduled_at).total_seconds()
        if delay < 0 or delay > self.grace_seconds:
            return None
        if scheduled_at.hour not in self.enabled_hours:
            return None
        key = self._key(scheduled_at)
        if key in self._emitted_keys:
            return None
        self._emitted_keys.add(key)
        return HourlyChimeEvent(
            scheduled_at=scheduled_at,
            hour=scheduled_at.hour,
            announcement=f"现在是 {scheduled_at.hour:02d} 点整",
        )

    def next_chime_after(
        self,
        now: datetime,
        *,
        inclusive: bool = False,
    ) -> datetime:
        """返回当前启用时段中的下一个整点。"""

        if not isinstance(now, datetime):
            raise TypeError("now 必须是 datetime")
        candidate = now.replace(minute=0, second=0, microsecond=0)
        exactly_on_hour = now == candidate
        if not (inclusive and exactly_on_hour):
            candidate += timedelta(hours=1)
        for _ in range(72):
            if candidate.hour in self.enabled_hours:
                return candidate
            candidate += timedelta(hours=1)
        raise RuntimeError("无法在启用时段中找到下一次整点")

    def reset_deduplication(self) -> None:
        """清空已发整点记录，供新会话或测试显式复位。"""

        self._emitted_keys.clear()


def random_integer(
    minimum: int,
    maximum: int,
    *,
    rng: IntegerRandomSource | None = None,
) -> int:
    """返回包含上下界的随机整数，并校验注入随机源的结果。"""

    if isinstance(minimum, bool) or not isinstance(minimum, int):
        raise TypeError("minimum 必须是整数")
    if isinstance(maximum, bool) or not isinstance(maximum, int):
        raise TypeError("maximum 必须是整数")
    if minimum > maximum:
        raise ValueError("minimum 不能大于 maximum")

    source = rng or random
    result = source.randint(minimum, maximum)
    if isinstance(result, bool) or not isinstance(result, int):
        raise TypeError("随机源必须返回整数")
    if not minimum <= result <= maximum:
        raise ValueError("随机源返回了范围外的值")
    return result


__all__ = [
    "CountUpTimer",
    "CountdownTimer",
    "HourlyChimeEvent",
    "HourlyChimeScheduler",
    "IntegerRandomSource",
    "PomodoroConfig",
    "PomodoroPhase",
    "PomodoroSnapshot",
    "PomodoroState",
    "PomodoroTransition",
    "TimerSnapshot",
    "random_integer",
]
