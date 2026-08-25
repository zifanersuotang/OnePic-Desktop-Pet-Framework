"""
本模块提供只统计按键活跃度、不采集按键内容的 Windows 输入活动探针。

职责范围：
- 轮询一组常用虚拟键是否从“未按下”变为“按下”；
- 只返回本轮新按下按键的数量，不返回键值、字符、窗口标题或输入文本；
- 在非 Windows、系统 API 不可用或测试环境中安全降级为零活动；
- 允许测试注入布尔状态读取器，不安装全局键盘钩子、不写文件、不访问网络。

该探针只用于触发桌宠的陪打字动画。调用方必须把统计保存在内存中，
不得把按键状态或活跃时间写入日志、设置文件或发布包。
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable, Iterable


MONITORED_VIRTUAL_KEYS = (
    0x08,  # Backspace
    0x09,  # Tab
    0x0D,  # Enter
    0x1B,  # Escape
    0x20,  # Space
    *range(0x25, 0x29),  # Arrow keys
    *range(0x30, 0x3A),  # 0-9
    *range(0x41, 0x5B),  # A-Z
    *range(0x60, 0x70),  # Numpad
    *range(0xBA, 0xC1),  # OEM punctuation
    *range(0xDB, 0xE0),
)


class KeyboardActivityMonitor:
    """统计新按下的常用键数量，同时避免暴露具体按键内容。"""

    def __init__(
        self,
        state_reader: Callable[[int], bool] | None = None,
        virtual_keys: Iterable[int] = MONITORED_VIRTUAL_KEYS,
    ) -> None:
        self._virtual_keys = tuple(int(value) for value in virtual_keys)
        self._state_reader = state_reader or self._load_windows_reader()
        self._previous_down: set[int] = set()

    @staticmethod
    def _load_windows_reader() -> Callable[[int], bool]:
        """加载 GetAsyncKeyState；失败时返回恒为 False 的安全读取器。"""

        if os.name != "nt":
            return lambda _key: False
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_async_key_state = user32.GetAsyncKeyState
            get_async_key_state.argtypes = [ctypes.c_int]
            get_async_key_state.restype = ctypes.c_short
        except (AttributeError, OSError):
            return lambda _key: False
        return lambda key: bool(get_async_key_state(int(key)) & 0x8000)

    def poll_pressed_count(self) -> int:
        """返回本轮从抬起变为按下的键数量，不返回任何键值。"""

        current_down = {
            key for key in self._virtual_keys if self._state_reader(key)
        }
        pressed_count = len(current_down - self._previous_down)
        self._previous_down = current_down
        return pressed_count

    def reset(self) -> None:
        """清空边沿状态，避免启用监测时把已按住的键计为新输入。"""

        self._previous_down.clear()
