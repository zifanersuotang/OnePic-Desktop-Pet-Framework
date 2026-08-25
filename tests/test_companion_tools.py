"""
本模块测试陪伴工具的正倒计时、番茄阶段、整点去重和范围随机数。

测试只传入虚拟时钟、固定日期与伪随机源，不创建线程、窗口或网络连接。
"""

from datetime import datetime, timedelta, timezone
import random

import pytest

from onepic_desktop_pet.companion.tools import (
    CountUpTimer,
    CountdownTimer,
    HourlyChimeScheduler,
    PomodoroConfig,
    PomodoroPhase,
    PomodoroState,
    random_integer,
)


def test_count_up_timer_can_pause_resume_and_reset() -> None:
    timer = CountUpTimer()

    assert timer.start(10.0) is True
    assert timer.start(11.0) is False
    assert timer.elapsed(13.5) == pytest.approx(3.5)
    assert timer.pause(14.0) is True
    assert timer.elapsed() == pytest.approx(4.0)

    assert timer.start(20.0) is True
    snapshot = timer.snapshot(22.25)
    assert snapshot.running is True
    assert snapshot.elapsed_seconds == pytest.approx(6.25)
    assert snapshot.remaining_seconds is None
    assert snapshot.finished is False

    timer.reset()
    assert timer.running is False
    assert timer.elapsed() == 0


def test_running_timer_requires_current_monotonic_time() -> None:
    timer = CountUpTimer()
    timer.start(5)

    with pytest.raises(ValueError, match="需要提供 now"):
        timer.elapsed()
    with pytest.raises(ValueError, match="不能早于"):
        timer.pause(4)


def test_countdown_pauses_resumes_and_only_finishes_once() -> None:
    timer = CountdownTimer(10)

    assert timer.start(100) is True
    assert timer.remaining(103) == pytest.approx(7)
    assert timer.pause(104) is True
    assert timer.remaining() == pytest.approx(6)
    assert timer.start(200) is True

    assert timer.snapshot(206).finished is True
    assert timer.sync(206) is True
    assert timer.sync(207) is False
    assert timer.finished is True
    assert timer.running is False
    assert timer.remaining() == 0
    assert timer.start(208) is False


def test_countdown_reset_can_change_duration() -> None:
    timer = CountdownTimer(30)
    timer.start(1)
    timer.reset(5)

    assert timer.duration_seconds == 5
    assert timer.remaining() == 5
    assert timer.running is False

    with pytest.raises(ValueError, match="非负"):
        timer.reset(-1)


def test_pomodoro_uses_short_breaks_then_long_break() -> None:
    config = PomodoroConfig(
        focus_seconds=10,
        short_break_seconds=3,
        long_break_seconds=8,
        focus_sessions_per_long_break=2,
    )
    state = PomodoroState(config)

    first = state.complete_phase()
    assert first.previous_phase is PomodoroPhase.FOCUS
    assert state.phase is PomodoroPhase.SHORT_BREAK
    assert state.remaining() == 3

    state.complete_phase()
    assert state.phase is PomodoroPhase.FOCUS
    second = state.complete_phase()
    assert second.completed_focus_sessions == 2
    assert state.phase is PomodoroPhase.LONG_BREAK
    assert state.remaining() == 8

    state.complete_phase()
    assert state.phase is PomodoroPhase.FOCUS
    assert state.focus_sessions_until_long_break == 2


def test_pomodoro_sync_transitions_once_and_can_auto_start() -> None:
    state = PomodoroState(
        PomodoroConfig(
            focus_seconds=5,
            short_break_seconds=2,
            long_break_seconds=3,
            focus_sessions_per_long_break=4,
        )
    )
    state.start(10)

    assert state.sync(14.9, auto_start_next=True) is None
    transition = state.sync(15, auto_start_next=True)

    assert transition is not None
    assert transition.next_phase is PomodoroPhase.SHORT_BREAK
    assert state.running is True
    assert state.remaining(16) == pytest.approx(1)
    assert state.sync(16) is None


def test_invalid_pomodoro_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="大于零"):
        PomodoroConfig(focus_seconds=0)
    with pytest.raises(ValueError, match="正整数"):
        PomodoroConfig(focus_sessions_per_long_break=0)


def test_hourly_chime_allows_polling_delay_and_deduplicates() -> None:
    scheduler = HourlyChimeScheduler(grace_seconds=90)
    due = datetime(2026, 8, 1, 17, 0, 45)

    event = scheduler.poll(due)

    assert event is not None
    assert event.hour == 17
    assert event.scheduled_at == due.replace(second=0)
    assert event.announcement == "现在是 17 点整"
    assert scheduler.poll(due + timedelta(seconds=10)) is None
    assert scheduler.poll(due + timedelta(minutes=2)) is None


def test_hourly_chime_respects_enabled_hours_and_finds_next_due() -> None:
    scheduler = HourlyChimeScheduler(
        grace_seconds=30,
        enabled_hours={9, 18},
    )
    morning = datetime(2026, 8, 1, 8, 30)

    assert scheduler.next_chime_after(morning) == datetime(2026, 8, 1, 9)
    assert scheduler.poll(datetime(2026, 8, 1, 10)) is None
    assert scheduler.next_chime_after(datetime(2026, 8, 1, 18)) == datetime(
        2026,
        8,
        2,
        9,
    )
    assert scheduler.next_chime_after(
        datetime(2026, 8, 1, 18),
        inclusive=True,
    ) == datetime(2026, 8, 1, 18)


def test_hourly_chime_keeps_timezone_and_can_explicitly_reset_dedup() -> None:
    china = timezone(timedelta(hours=8))
    now = datetime(2026, 8, 1, 23, tzinfo=china)
    scheduler = HourlyChimeScheduler()

    assert scheduler.poll(now) is not None
    assert scheduler.poll(now) is None
    scheduler.reset_deduplication()
    assert scheduler.poll(now) is not None
    assert scheduler.next_chime_after(now).utcoffset() == timedelta(hours=8)


class _UpperBoundRng:
    def randint(self, minimum: int, maximum: int, /) -> int:
        return maximum


class _BrokenRng:
    def randint(self, minimum: int, maximum: int, /) -> int:
        return maximum + 1


def test_random_integer_is_inclusive_and_rng_is_injectable() -> None:
    assert random_integer(4, 9, rng=_UpperBoundRng()) == 9
    assert random_integer(7, 7, rng=random.Random(42)) == 7


def test_random_integer_rejects_invalid_ranges_and_random_sources() -> None:
    with pytest.raises(ValueError, match="不能大于"):
        random_integer(3, 2)
    with pytest.raises(TypeError, match="必须是整数"):
        random_integer(True, 2)
    with pytest.raises(ValueError, match="范围外"):
        random_integer(1, 2, rng=_BrokenRng())
