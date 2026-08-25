"""
本模块验证智能陪伴窗口能离屏创建、持久化与操作纯本地工具。

测试不发起网络请求，不弹出系统托盘，数据和 DPAPI 密文路径均位于
pytest 局部沙箱。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox

from onepic_desktop_pet.companion.chat import ChatReply
from onepic_desktop_pet.companion.qt_chat import QtChatRequest
from onepic_desktop_pet.companion.secrets import ApiKeyStore
from onepic_desktop_pet.companion.storage import CompanionStorage
from onepic_desktop_pet.companion.ui import (
    CompanionController,
    _endpoint_contains_secret_query,
)
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.window import PetWindow


def _create_controller(
    root: Path,
) -> tuple[QApplication, PetWindow, CompanionController]:
    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings())
    controller = CompanionController(
        window,
        storage=CompanionStorage(root / "companion.sqlite3"),
        key_store=ApiKeyStore(root / "api-key.dpapi"),
    )
    window.attach_companion_controller(controller)
    return app, window, controller


def _dispose(
    app: QApplication,
    window: PetWindow,
    controller: CompanionController,
) -> None:
    controller.shutdown()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_companion_menu_and_center_persist_profiles(tmp_path: Path) -> None:
    app, window, controller = _create_controller(tmp_path)
    menu = QMenu(window)
    controller.populate_menu(menu)

    companion_action = next(
        action for action in menu.actions() if action.text() == "智能陪伴"
    )
    submenu = companion_action.menu()
    assert submenu is not None
    labels = [action.text() for action in submenu.actions()]
    assert "实时聊天" in labels
    assert "番茄钟 / 计时器" in labels
    assert "扫雷" in labels

    center = controller.open_center("profiles")
    assert center.tabs.currentIndex() == 1
    center.character_name.setText("测试桌宠")
    center.character_persona.setPlainText("诚实、简洁，不虚构经历。")
    center.user_name.setText("委托人")
    center.save_profiles()

    assert controller.storage.get_character_profile("demo") == {
        "name": "测试桌宠",
        "persona": "诚实、简洁，不虚构经历。",
    }
    assert controller.storage.get_user_profile("demo")["preferred_name"] == "委托人"

    menu.deleteLater()
    _dispose(app, window, controller)


def test_compact_chat_is_chat_only_and_opening_does_not_start_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """双击入口对应独立紧凑聊天页，打开窗口本身绝不联网。"""

    app, window, controller = _create_controller(tmp_path)
    starts: list[object] = []
    monkeypatch.setattr(
        QtChatRequest,
        "start",
        lambda self, **request: starts.append(request),
    )

    compact = controller.open_compact_chat()
    app.processEvents()

    assert compact.windowTitle() == "OnePic 快捷聊天"
    assert compact.tabs.count() == 1
    assert compact.tabs.tabText(0) == "聊天"
    assert "打开此窗口不会联网" in compact.privacy_notice.text()
    assert starts == []

    full = controller.open_center("api")
    assert full is not compact
    assert full.tabs.count() == 7
    assert full.tabs.currentIndex() == 6
    _dispose(app, window, controller)


def test_memory_is_not_selected_for_cloud_chat_by_default(tmp_path: Path) -> None:
    app, window, controller = _create_controller(tmp_path)
    center = controller.open_center("memory")
    memory_id = controller.storage.save_memory(
        "demo",
        "只在明确勾选后使用",
        title="约定",
    )
    center.refresh_memories()

    assert center.selected_memories() == []
    item = center.memory_list.item(0)
    assert item.data(Qt.ItemDataRole.UserRole) == memory_id
    item.setCheckState(Qt.CheckState.Checked)
    assert [record["memory_id"] for record in center.selected_memories()] == [memory_id]

    _dispose(app, window, controller)


def test_cloud_privacy_notice_discloses_complete_request_context(
    tmp_path: Path,
) -> None:
    """联网提示必须覆盖实际进入请求的每类上下文。"""

    app, window, controller = _create_controller(tmp_path)
    center = controller.open_center("api")
    notice = center.privacy_notice.text()

    for disclosed in (
        "当前消息",
        "角色/委托人档案",
        "只读好感度",
        "勾选的记忆",
        "本次预设文本",
        "情绪/动作白名单",
        "聊天历史默认不发送",
    ):
        assert disclosed in notice

    _dispose(app, window, controller)


def test_chat_history_is_not_sent_without_explicit_opt_in(tmp_path: Path) -> None:
    app, window, controller = _create_controller(tmp_path)
    controller.storage.add_message("demo", "user", "只保存在本地的历史")
    center = controller.open_center("api")

    assert center.api_include_history.isChecked() is False
    assert center.history_for_request({}, pet_id="demo") == []
    history = center.history_for_request(
        {"include_history": True},
        pet_id="demo",
    )
    assert [message["content"] for message in history] == ["只保存在本地的历史"]

    _dispose(app, window, controller)


def test_send_chat_payload_obeys_history_opt_in_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window, controller = _create_controller(tmp_path)
    controller.storage.add_message("demo", "user", "旧历史")
    controller.storage.set_tool_settings(
        "demo",
        "ai_chat",
        {
            "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
            "model": "local-test",
            "stream": False,
            "include_history": False,
            "timeout": 30,
        },
        enabled=True,
    )
    center = controller.open_center("chat")
    captured: list[dict[str, object]] = []

    def capture_start(
        _request: QtChatRequest,
        **request: object,
    ) -> None:
        captured.append(request)

    monkeypatch.setattr(QtChatRequest, "start", capture_start)
    center.chat_input.setPlainText("新消息")
    center.send_chat()

    messages = captured[0]["messages"]
    assert isinstance(messages, list)
    assert [message["content"] for message in messages[1:]] == ["新消息"]

    center._chat_finished()
    settings = controller.storage.get_tool_settings("demo", "ai_chat")
    assert settings is not None
    updated = dict(settings["settings"])
    updated["include_history"] = True
    controller.storage.set_tool_settings(
        "demo",
        "ai_chat",
        updated,
        enabled=True,
    )
    center.chat_input.setPlainText("再问一次")
    center.send_chat()
    messages = captured[1]["messages"]
    assert isinstance(messages, list)
    assert [message["content"] for message in messages[1:]] == [
        "旧历史",
        "再问一次",
    ]

    _dispose(app, window, controller)


def test_late_reply_stays_bound_to_original_pet_after_switch(tmp_path: Path) -> None:
    app, window, controller = _create_controller(tmp_path)
    center = controller.open_center("chat")
    center._pending_pet_id = "demo"
    center._pending_user_text = "A 宠物的问题"

    window._active_pet_id = "pet-b"
    window.settings.pet_id = "pet-b"
    controller.on_pet_changed()
    assert center.pet_id == "pet-b"

    center._chat_succeeded(ChatReply("只能写回 A 宠物"))

    assert [
        message["content"]
        for message in controller.storage.list_messages("demo")
    ] == ["A 宠物的问题", "只能写回 A 宠物"]
    assert controller.storage.list_messages("pet-b") == []
    assert "只能写回 A 宠物" not in center.chat_output.toPlainText()

    _dispose(app, window, controller)


def test_timer_and_minesweeper_windows_use_local_models(tmp_path: Path) -> None:
    app, window, controller = _create_controller(tmp_path)

    timer = controller.open_timer()
    assert timer.tabs.count() == 3
    timer._start_countup()
    assert timer._countup.running is True
    timer._pause_countup()
    assert timer._countup.running is False

    minesweeper = controller.open_minesweeper()
    minesweeper._activate_cell(0, 0, False)
    assert minesweeper.model.first_click_done is True
    assert (0, 0) not in minesweeper.model.mine_positions
    assert minesweeper.model.cell(0, 0).state.value == "revealed"
    minesweeper.reset_game()
    assert minesweeper.model.first_click_done is False

    _dispose(app, window, controller)


def test_controller_shutdown_cancels_chat_without_waiting_for_thread(
    tmp_path: Path,
) -> None:
    app, window, controller = _create_controller(tmp_path)
    center = controller.open_center("chat")

    class _ActiveRequest:
        active = True
        cancelled = False

        def cancel(self) -> None:
            self.cancelled = True
            self.active = False

    request = _ActiveRequest()
    center._chat_request = request  # type: ignore[assignment]

    controller.shutdown()

    assert request.cancelled is True
    window.close()
    window.deleteLater()
    app.processEvents()


def test_api_endpoint_query_cannot_smuggle_plaintext_secret() -> None:
    assert _endpoint_contains_secret_query(
        "https://example.test/v1/chat/completions?api_key=plaintext"
    )
    assert _endpoint_contains_secret_query(
        "https://example.test/v1/chat/completions?access-token=plaintext"
    )
    assert not _endpoint_contains_secret_query(
        "https://example.test/v1/chat/completions?api_version=2026-08-01"
    )


def test_api_ui_rejects_out_of_range_port_without_unhandled_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存和读取到畸形端口时都只显示提示，不进入密钥或网络层。"""

    app, window, controller = _create_controller(tmp_path)
    center = controller.open_center("api")
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message, *_args, **_kwargs: warnings.append(
            str(message)
        ),
    )

    center.api_endpoint.setText("https://example.test:99999/v1")
    center.save_api_settings()
    assert warnings and "端口" in warnings[-1]
    assert controller.storage.get_tool_settings("demo", "ai_chat") is None

    controller.storage.set_tool_settings(
        "demo",
        "ai_chat",
        {
            "endpoint": "https://example.test:99999/v1",
            "model": "tampered",
            "stream": False,
            "timeout": 30,
        },
        enabled=True,
    )
    center.chat_input.setPlainText("不要发出")
    center.send_chat()
    assert "端口" in warnings[-1]
    assert center._chat_request.active is False

    _dispose(app, window, controller)


def test_controller_never_reuses_key_across_pet_or_endpoint(
    tmp_path: Path,
) -> None:
    app, window, controller = _create_controller(tmp_path)

    first = controller.key_store_for("demo", "https://api.example.test/v1")
    same_origin = controller.key_store_for(
        "demo",
        "https://api.example.test/other/path",
    )
    another_pet = controller.key_store_for(
        "pet-b",
        "https://api.example.test/v1",
    )
    another_origin = controller.key_store_for(
        "demo",
        "https://other.example.test/v1",
    )

    assert first.path == same_origin.path
    assert first.path != another_pet.path
    assert first.path != another_origin.path

    _dispose(app, window, controller)


def test_export_and_delete_controls_exclude_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window, controller = _create_controller(tmp_path)
    center = controller.open_center("api")
    controller.storage.set_character_profile("demo", {"name": "可导出角色"})
    export_path = tmp_path / "export.json"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(export_path), "JSON (*.json)"),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok,
    )
    center.export_local_data()

    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["character_profile"]["name"] == "可导出角色"
    assert "api_key" not in export_path.read_text(encoding="utf-8").casefold()

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    center.delete_local_data()
    assert controller.storage.get_character_profile("demo") is None
    assert controller.storage.list_preset_commands("demo")

    _dispose(app, window, controller)
