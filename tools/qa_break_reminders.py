"""
本工具在真实 Windows Qt 运行时中加速验收普通桌宠内置休息提醒。

职责范围：
- 启动同一个 OnePic PetWindow、同一个托盘菜单和内置提醒控制器；
- 依次验证预告、到点、延期、跳过、暂停、恢复、番茄阶段、完成和庆祝；
- 只抓取桌宠、气泡、提醒卡和菜单控件，合成到中性 QA 画布，不截取用户桌面；
- 记录屏幕数量、几何、DPR、真实 Win32 idle/fullscreen 探针返回值和加速方式；
- 把截图和 JSON 日志写入用户指定目录，退出时停止全部 Qt 计时器。

脚本不会改开机启动、不会启用声音或统计、不会访问网络。它不能替代真实游戏、
休眠唤醒、多显示器硬件和长期 25 分钟运行测试，日志会明确保留这些边界。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from onepic_desktop_pet.app import DesktopPetApplication
from onepic_desktop_pet.break_scheduler import (
    is_fullscreen_foreground,
    seconds_since_input,
)


def _capture_widgets(
    output: Path,
    name: str,
    widgets: list[QWidget],
) -> Path:
    """把可见 Qt 顶层控件按真实位置合成到中性背景。"""

    visible = [widget for widget in widgets if widget.isVisible()]
    if not visible:
        raise RuntimeError(f"截图步骤没有可见控件：{name}")
    bounds = QRect(visible[0].frameGeometry())
    for widget in visible[1:]:
        bounds = bounds.united(widget.frameGeometry())
    padding = 24
    canvas = QPixmap(bounds.width() + padding * 2, bounds.height() + padding * 2)
    canvas.fill(QColor("#dff7ef"))
    painter = QPainter(canvas)
    for widget in visible:
        offset = widget.frameGeometry().topLeft() - bounds.topLeft()
        painter.drawPixmap(offset + QPoint(padding, padding), widget.grab())
    painter.end()
    target = output / f"{name}.png"
    if not canvas.save(str(target)):
        raise RuntimeError(f"无法保存截图：{target}")
    return target


def run_qa(output: Path, *, use_demo_assets: bool = True) -> int:
    """运行顺序化 GUI 验收并返回 Qt 事件循环退出码。"""

    output.mkdir(parents=True, exist_ok=True)
    if use_demo_assets:
        os.environ["ONEPIC_USE_DEMO_ASSETS"] = "1"
    else:
        os.environ.pop("ONEPIC_USE_DEMO_ASSETS", None)
    os.environ["ONEPIC_BREAK_TEST_MODE"] = "1"
    os.environ["ONEPIC_SETTINGS_DIR"] = str(output / "local-settings")
    os.environ["LOCALAPPDATA"] = str(output / "local-app-data")

    desktop = DesktopPetApplication()
    controller = desktop.break_reminders
    if controller is None:
        raise RuntimeError("休息提醒控制器未能启动")
    controller.monitor_system_state = False
    controller.tick_timer.stop()
    controller.status_timer.stop()
    controller.toggle_enabled(True)
    controller.set_sound_enabled(False)
    controller.set_statistics_enabled(False)

    app = QApplication.instance()
    assert app is not None
    manifest_path = desktop.window.active_manifest_path()
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    log: list[dict[str, object]] = []
    screens = [
        {
            "name": screen.name(),
            "geometry": [
                screen.geometry().x(),
                screen.geometry().y(),
                screen.geometry().width(),
                screen.geometry().height(),
            ],
            "available_geometry": [
                screen.availableGeometry().x(),
                screen.availableGeometry().y(),
                screen.availableGeometry().width(),
                screen.availableGeometry().height(),
            ],
            "device_pixel_ratio": screen.devicePixelRatio(),
            "logical_dpi": screen.logicalDotsPerInch(),
        }
        for screen in QApplication.screens()
    ]
    log.append(
        {
            "step": "environment",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", "windows(default)"),
            "asset_source": "demo" if use_demo_assets else "active",
            "pet_id": manifest_data.get("pet_id"),
            "display_name": manifest_data.get("display_name"),
            "active_manifest_file": manifest_path.name,
            "system_tray_available": desktop.tray.isSystemTrayAvailable(),
            "screens": screens,
            "actual_idle_probe_seconds": seconds_since_input(),
            "actual_fullscreen_probe": is_fullscreen_foreground(),
            "capture_method": (
                "真实 Qt 控件 grab，按窗口坐标合成到薄荷色中性 QA 画布；"
                "未截取桌面背景"
            ),
            "timer_mode": "ONEPIC_BREAK_TEST_MODE=1 + 直接状态操作",
            "system_probe_in_sequence": (
                "为避免当前桌面状态让脚本不确定，流程步骤禁用探针；环境步骤单独真实调用"
            ),
            "limitations": [
                "真实游戏/反作弊环境未验证",
                "系统休眠唤醒未验证",
                "只有检测到的当前显示器参与真实 GUI 验收",
                "没有真实等待 25 分钟，番茄阶段使用加速配置和直接转换",
            ],
        }
    )

    window = desktop.window

    def record(
        name: str,
        action: Callable[[], None],
        *,
        include_panel: bool = True,
        include_bubble: bool = True,
    ) -> None:
        action()
        app.processEvents()
        widgets: list[QWidget] = [window]
        if include_bubble and window.dialogue_bubble.isVisible():
            widgets.append(window.dialogue_bubble)
        if include_panel and controller.panel.isVisible():
            widgets.append(controller.panel)
        screenshot = _capture_widgets(output, name, widgets)
        log.append(
            {
                "step": name,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "screenshot": screenshot.name,
                "pet_state": window.state.value,
                "panel_visible": controller.panel.isVisible(),
                "bubble_visible": window.dialogue_bubble.isVisible(),
                "engine": controller.engine.snapshot(),
                "pomodoro_phase": (
                    controller.pomodoro.phase.value
                    if controller.pomodoro is not None
                    else None
                ),
            }
        )

    steps: list[Callable[[], None]] = []
    steps.append(
        lambda: record(
            "gui-01-idle",
            lambda: window.present_system_reminder("curious", "休息提醒模块已就绪。"),
            include_panel=False,
        )
    )
    steps.append(
        lambda: record(
            "gui-02-preview",
            controller.show_manual_preview,
            include_panel=False,
        )
    )
    steps.append(
        lambda: record(
            "gui-03-active",
            lambda: controller.trigger_reminder("micro"),
        )
    )
    steps.append(
        lambda: record("gui-04-snooze", controller.snooze_current)
    )
    steps.append(
        lambda: record(
            "gui-05-active-again",
            lambda: controller.trigger_reminder("water"),
        )
    )
    steps.append(lambda: record("gui-06-skip", controller.skip_current))
    steps.append(lambda: record("gui-07-pause", controller.toggle_pause))
    steps.append(lambda: record("gui-08-resume", controller.toggle_pause))
    steps.append(
        lambda: record(
            "gui-09-pomodoro-focus",
            lambda: controller.restart_pomodoro("pomodoro"),
            include_panel=False,
        )
    )

    def enter_pomodoro_break() -> None:
        assert controller.pomodoro is not None
        transition = controller.pomodoro.complete_phase(
            start_next_at=controller.clock()
        )
        controller._handle_pomodoro_transition(transition)

    steps.append(
        lambda: record("gui-10-pomodoro-break", enter_pomodoro_break)
    )
    steps.append(
        lambda: record("gui-11-pomodoro-complete", controller.complete_current)
    )
    steps.append(
        lambda: record(
            "gui-12-celebrate",
            lambda: window.present_system_reminder(
                "happy",
                "休息也是认真生活的一部分。",
            ),
            include_panel=False,
        )
    )

    def capture_tray_menu() -> None:
        desktop.tray_menu.popup(window.pos() + QPoint(20, 20))
        app.processEvents()
        target = output / "gui-13-tray-menu.png"
        if not desktop.tray_menu.grab().save(str(target)):
            raise RuntimeError("托盘菜单截图保存失败")
        log.append(
            {
                "step": "gui-13-tray-menu",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "screenshot": target.name,
                "menu_actions": [
                    action.text() for action in desktop.tray_menu.actions()
                ],
                "note": "真实 QMenu 控件截图；不是系统托盘弹出区域的整屏截图",
            }
        )
        desktop.tray_menu.close()

    steps.append(capture_tray_menu)

    def move_and_capture() -> None:
        window.move(window.pos() + QPoint(-54, -36))
        record(
            "gui-14-drag-position",
            lambda: window.present_system_reminder(
                "curious",
                "角色位置可拖动，提醒卡会跟随定位。",
            ),
            include_panel=False,
        )

    steps.append(move_and_capture)

    state = {"index": 0}

    def next_step() -> None:
        if state["index"] >= len(steps):
            (output / "gui-qa-log.json").write_text(
                json.dumps(log, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            desktop.quit()
            return
        steps[state["index"]]()
        state["index"] += 1
        QTimer.singleShot(420, next_step)

    QTimer.singleShot(500, next_step)
    return desktop.start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 OnePic 休息提醒 GUI QA")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--active-assets",
        action="store_true",
        help="使用当前已激活桌宠素材；默认继续使用公开演示素材",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_qa(
        args.output.resolve(),
        use_demo_assets=not args.active_assets,
    )


if __name__ == "__main__":
    raise SystemExit(main())
