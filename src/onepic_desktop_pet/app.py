"""
本模块管理桌面宠物应用生命周期、系统托盘菜单和退出时的用户偏好保存。

职责范围：
- 创建或复用 QApplication；
- 在创建应用前启用适合不同显示器缩放比例的高 DPI 舍入策略；
- 创建 PetWindow 和 QSystemTrayIcon；
- 创建可选智能陪伴控制器，把档案、历史、计时工具、小游戏和用户主动配置的聊天
  接到现有窗口；陪伴数据目录不可写或陪伴标识无效时保持基础桌宠可运行，
  并在托盘显示不含异常细节的不可用提示；
- 创建可选休息提醒控制器，把健康周期、角色番茄钟和持久操作卡接入同一托盘；
  提醒配置错误时基础桌宠仍可运行；
- 连接显示、隐藏、暂停跑动、互动和退出动作；
- 退出前将窗口位置、尺寸、可选活动区域、走路样式、自定义台词和按宠物隔离的
  养成状态写入设置文件；
- 为自动验证提供定时退出的 smoke-test 参数。

Agent 快速定位：
- 生命周期封装位于 DesktopPetApplication；
- 托盘菜单构建位于 _create_tray()；
- 持久化与退出位于 quit()；
- 外部调用入口位于 run()。

输入为可选的无界面冒烟测试时长，输出为 Qt 事件循环退出码。
副作用包括创建桌面窗口、托盘图标和用户设置文件；不修改项目默认配置或原始素材。
"""

from __future__ import annotations

import sqlite3
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .config import PetSettings, load_settings, save_settings
from .break_reminder_ui import BreakReminderController
from .companion.ui import CompanionController
from .resources import resource_path
from .window import PetWindow


class DesktopPetApplication:
    """封装窗口、托盘与持久化状态的桌面宠物应用。"""

    def __init__(self, settings: PetSettings | None = None) -> None:
        if QApplication.instance() is None:
            QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
        self.qt_app = QApplication.instance() or QApplication(sys.argv)
        self.qt_app.setApplicationName("OnePic Desktop Pet")
        self.qt_app.setQuitOnLastWindowClosed(False)
        self.settings = settings or load_settings()
        self.window = PetWindow(self.settings)
        self.break_reminders: BreakReminderController | None = None
        self.break_reminder_unavailable_notice: str | None = None
        try:
            self.break_reminders = BreakReminderController(self.window)
        except (OSError, ValueError):
            self.break_reminders = None
            self.break_reminder_unavailable_notice = (
                "休息提醒配置不可用（基础桌宠仍可运行）"
            )
        self.window.attach_break_reminder_controller(self.break_reminders)
        self.companion: CompanionController | None = None
        self.companion_unavailable_notice: str | None = None
        try:
            self.companion = CompanionController(self.window)
        except (OSError, sqlite3.DatabaseError, ValueError):
            # 陪伴层属于可选功能；数据或标识无效时不得拖垮基础桌宠。
            self.companion = None
            self.companion_unavailable_notice = (
                "智能陪伴不可用（请重启或检查本地数据/宠物 ID）"
            )
        self.window.attach_companion_controller(self.companion)
        self.window.quit_requested.connect(self.quit)
        self.tray = self._create_tray()

    def _create_tray(self) -> QSystemTrayIcon:
        """创建系统托盘图标及其操作菜单。"""

        icon = QIcon(str(resource_path("assets/icons/pet.png")))
        tray = QSystemTrayIcon(icon, self.qt_app)
        tray.setToolTip("OnePic Desktop Pet")
        menu = QMenu()

        show_action = QAction("显示宠物", menu)
        show_action.triggered.connect(self.show_window)
        menu.addAction(show_action)

        interact_action = QAction("和她打招呼", menu)
        interact_action.triggered.connect(self.window.trigger_interaction)
        menu.addAction(interact_action)

        selfie_action = QAction("自拍一下", menu)
        selfie_action.triggered.connect(self.window.trigger_selfie)
        menu.addAction(selfie_action)

        if self.companion is not None:
            companion_action = QAction("智能陪伴中心", menu)
            companion_action.triggered.connect(
                lambda _checked=False: self.companion.open_center()
            )
            menu.addAction(companion_action)
        elif self.companion_unavailable_notice:
            unavailable_action = QAction(self.companion_unavailable_notice, menu)
            unavailable_action.setEnabled(False)
            menu.addAction(unavailable_action)

        if self.break_reminders is not None:
            self.break_reminders.populate_menu(menu)
        elif self.break_reminder_unavailable_notice:
            unavailable_action = QAction(
                self.break_reminder_unavailable_notice,
                menu,
            )
            unavailable_action.setEnabled(False)
            menu.addAction(unavailable_action)

        pause_action = QAction("暂停/恢复跑动", menu)
        pause_action.triggered.connect(
            lambda: self.window.set_paused(not self.window.paused)
        )
        menu.addAction(pause_action)

        hide_action = QAction("隐藏宠物", menu)
        hide_action.triggered.connect(self.window.hide)
        menu.addAction(hide_action)
        menu.addSeparator()

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        self.tray_menu = menu
        tray.activated.connect(self._tray_activated)
        return tray

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """单击或双击托盘图标时显示宠物。"""

        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def show_window(self) -> None:
        """显示宠物并将其提升到前台。"""

        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def start(self, smoke_test_ms: int | None = None) -> int:
        """显示应用并进入事件循环；可选定时退出用于自动验证。"""

        self.window.place_at_start()
        self.show_window()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        if smoke_test_ms is not None:
            QTimer.singleShot(max(1, smoke_test_ms), self.quit)
        return self.qt_app.exec()

    def quit(self) -> None:
        """保存窗口位置、隐藏托盘并退出应用。"""

        self.settings.start_x = self.window.x()
        self.settings.start_y = self.window.y()
        self.window.export_care_state()
        if self.companion is not None:
            self.companion.shutdown()
        if self.break_reminders is not None:
            self.break_reminders.shutdown()
        try:
            save_settings(self.settings)
        finally:
            self.tray.hide()
            self.window.close()
            self.qt_app.quit()


def run(smoke_test_ms: int | None = None) -> int:
    """创建并运行桌面宠物应用。"""

    return DesktopPetApplication().start(smoke_test_ms=smoke_test_ms)
