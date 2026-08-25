"""
本模块验证智能陪伴层与现有 PetWindow 之间的最小安全桥接及失败降级。

测试只创建离屏 Qt 窗口，不访问网络、不写陪伴数据库，也不启动系统托盘。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.app import DesktopPetApplication
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.window import PetWindow


class _FakeCompanionController:
    """仅向测试菜单增加一个可识别动作。"""

    def populate_menu(self, menu: QMenu) -> None:
        menu.addAction(QAction("智能陪伴测试", menu))


def test_window_adds_companion_menu_without_changing_default_assets() -> None:
    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings())
    window.attach_companion_controller(_FakeCompanionController())

    menu = window._build_context_menu()
    assert "智能陪伴测试" in [action.text() for action in menu.actions()]
    assert window.companion_pet_id() == "demo"

    menu.deleteLater()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_companion_action_bridge_only_accepts_loaded_whitelist() -> None:
    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings())

    assert window.trigger_companion_action("happy") is True
    assert window.state is PetState.HAPPY
    assert window.trigger_companion_action("powershell -Command whoami") is False
    assert window.trigger_companion_action("https://example.com") is False

    window.close()
    window.deleteLater()
    app.processEvents()


def test_companion_text_is_visible_when_optional_dialogue_pack_is_disabled() -> None:
    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings())
    assert window._dialogue_config.get("enabled", False) is False

    window.show()
    window.show_companion_text("倒计时结束了。")
    app.processEvents()

    assert window.dialogue_bubble.isVisible() is True
    assert window.dialogue_bubble.text() == "倒计时结束了。"

    window.close()
    window.deleteLater()
    app.processEvents()


def test_invalid_companion_pet_id_does_not_prevent_base_pet_startup() -> None:
    """可选陪伴层拒绝旧式宠物名时，基础窗口与托盘仍应完成创建。"""

    qt_app = QApplication.instance() or QApplication([])
    desktop = DesktopPetApplication(PetSettings(pet_id="中文 宠物"))

    assert desktop.window is not None
    assert desktop.companion is None
    assert desktop.window._companion_controller is None
    tray_actions = desktop.tray_menu.actions()
    assert "智能陪伴中心" not in [action.text() for action in tray_actions]
    unavailable = next(
        action for action in tray_actions if action.text().startswith("智能陪伴不可用")
    )
    assert unavailable.isEnabled() is False

    desktop.tray.hide()
    desktop.tray.deleteLater()
    desktop.window.close()
    desktop.window.deleteLater()
    qt_app.processEvents()
