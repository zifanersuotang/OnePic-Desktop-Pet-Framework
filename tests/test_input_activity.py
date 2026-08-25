"""
本模块验证陪打字输入探针只统计按下边沿，不暴露具体键值或文本。

测试使用内存状态读取器，不调用真实 Windows 键盘 API、不安装钩子、
不写文件，也不生成任何可还原用户输入内容的记录。
"""

from __future__ import annotations

from onepic_desktop_pet.input_activity import KeyboardActivityMonitor


def test_keyboard_activity_monitor_counts_only_new_press_edges() -> None:
    """按住不应重复计数，抬起后再次按下才产生新活跃事件。"""

    down: set[int] = set()
    monitor = KeyboardActivityMonitor(
        state_reader=lambda key: key in down,
        virtual_keys=(1, 2, 3),
    )

    assert monitor.poll_pressed_count() == 0
    down.update({1, 2})
    assert monitor.poll_pressed_count() == 2
    assert monitor.poll_pressed_count() == 0
    down.remove(1)
    assert monitor.poll_pressed_count() == 0
    down.add(1)
    assert monitor.poll_pressed_count() == 1


def test_keyboard_activity_monitor_reset_forgets_previous_down_state() -> None:
    """重置后仍只返回数量，且不会公开当前按下的是哪个键。"""

    down = {7}
    monitor = KeyboardActivityMonitor(
        state_reader=lambda key: key in down,
        virtual_keys=(7, 8),
    )

    assert monitor.poll_pressed_count() == 1
    monitor.reset()
    assert monitor.poll_pressed_count() == 1
