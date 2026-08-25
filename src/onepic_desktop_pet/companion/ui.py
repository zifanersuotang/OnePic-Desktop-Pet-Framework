"""
本模块实现 OnePic 智能陪伴框架的 PySide6 控制器与用户界面。

职责范围：
- 在现有宠物右键菜单中增加完整陪伴中心，并提供双击直达的独立紧凑聊天页；
- 编辑按宠物隔离的人设、用户档案、历史、记忆和文本预设指令；
- 通过 Qt 异步网络调用用户主动配置的 OpenAI-compatible 服务，
  支持流式显示、立即取消和无阻塞退出；
- 在联网前完整披露发送的档案、好感度、记忆、预设、白名单与可选历史；
- 管理番茄钟、正倒计时、整点报时、随机数和扫雷窗口；
- 让用户导出或删除当前宠物的本地陪伴数据；
- 只通过 PetWindow 的纯文本气泡与动作白名单桥接模型结果。

本模块不会上传图片或 ``user_assets`` 内容。云聊默认关闭；API 密钥只交给
``ApiKeyStore`` 的 Windows DPAPI 实现，SQLite 与 UI 日志都不保存明文密钥。
"""

from __future__ import annotations

import json
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from .chat import (
    ChatReply,
    build_messages,
    resolve_chat_completions_url,
)
from .framework import DEFAULT_PRESET_COMMANDS, normalize_pet_id
from .secrets import ApiKeyStore, SecretStorageError
from .storage import CompanionStorage
from .qt_chat import QtChatRequest
from .tools import (
    CountdownTimer,
    CountUpTimer,
    HourlyChimeScheduler,
    PomodoroConfig,
    PomodoroPhase,
    PomodoroState,
    random_integer,
)
from .games import CellState, MinesweeperModel, MinesweeperStatus

if TYPE_CHECKING:
    from onepic_desktop_pet.window import PetWindow


_DEFAULT_ENDPOINT = "https://api.openai.com/v1"
_DEFAULT_MODEL = ""
_BASE_EMOTIONS = (
    "happy",
    "shy",
    "surprised",
    "annoyed",
    "sleepy",
    "curious",
)
_BASE_ACTIONS = (
    "wave",
    "happy",
    "shy",
    "surprised",
    "annoyed",
    "sleepy",
    "curious",
)


def _is_loopback_endpoint(endpoint: str) -> bool:
    """返回端点是否显式指向本机，避免把普通 HTTP 误当作安全云服务。"""

    host = (urllib.parse.urlsplit(endpoint).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _endpoint_contains_secret_query(endpoint: str) -> bool:
    """返回 URL 查询参数名是否像密钥。

    服务地址会写入 SQLite，因此即使用户主动粘贴了含 token 的
    URL，也要阻止其绕过 DPAPI 密钥存储。
    """

    suspicious_fragments = ("key", "token", "secret", "password", "credential")
    for key, _value in urllib.parse.parse_qsl(
        urllib.parse.urlsplit(endpoint).query,
        keep_blank_values=True,
    ):
        normalized = key.casefold().replace("-", "_")
        if any(fragment in normalized for fragment in suspicious_fragments):
            return True
    return False


class CompanionController(QObject):
    """连接 PetWindow、陪伴数据层和各个非模态窗口。"""

    def __init__(
        self,
        window: PetWindow,
        *,
        storage: CompanionStorage | None = None,
        key_store: ApiKeyStore | None = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.storage = storage or CompanionStorage()
        self.key_store = key_store or ApiKeyStore()
        self._key_store_directory = self.key_store.path.parent
        self._center: CompanionCenterDialog | None = None
        self._compact_chat: CompanionCenterDialog | None = None
        self._timer_dialog: CompanionTimerDialog | None = None
        self._minesweeper_dialog: MinesweeperDialog | None = None
        self._hourly_scheduler = HourlyChimeScheduler()
        self._hourly_timer = QTimer(self)
        self._hourly_timer.setInterval(15_000)
        self._hourly_timer.timeout.connect(self._hourly_tick)
        self._hourly_timer.start()
        self._seed_default_presets()

    def pet_id(self) -> str:
        """返回当前窗口对应的安全宠物标识。"""

        return normalize_pet_id(self.window.companion_pet_id())

    def bond(self) -> float:
        """返回只读好感值，模型不能用它修改养成存档。"""

        return self.window.companion_bond()

    def allowed_actions(self) -> tuple[str, ...]:
        """返回基础白名单与当前已装配扩展动作的安全交集。"""

        loaded = tuple(
            str(name)
            for name in getattr(self.window, "_extension_animations", {})
        )
        return tuple(dict.fromkeys((*_BASE_ACTIONS, *loaded)))

    def key_store_for(self, pet_id: str, endpoint: str) -> ApiKeyStore:
        """返回与宠物及端点 origin 双重绑定的 DPAPI 存储。"""

        return ApiKeyStore.for_binding(
            normalize_pet_id(pet_id),
            endpoint,
            self._key_store_directory,
        )

    def _seed_default_presets(self, pet_id: str | None = None) -> None:
        """只在当前宠物没有任何预设时写入三条原创文本模板。"""

        target_pet_id = normalize_pet_id(pet_id or self.pet_id())
        if self.storage.list_preset_commands(target_pet_id):
            return
        for order, command in enumerate(DEFAULT_PRESET_COMMANDS):
            self.storage.save_preset_command(
                target_pet_id,
                command.key,
                command.label,
                command.prompt,
                sort_order=order,
            )

    def populate_menu(self, menu: QMenu) -> None:
        """向现有宠物菜单添加一组由控制器管理的动作。"""

        companion_menu = menu.addMenu("智能陪伴")
        self._add_menu_action(
            companion_menu,
            "打开陪伴中心",
            lambda: self.open_center("chat"),
        )
        companion_menu.addSeparator()
        self._add_menu_action(
            companion_menu,
            "实时聊天",
            lambda: self.open_center("chat"),
        )
        self._add_menu_action(
            companion_menu,
            "人设与委托人档案",
            lambda: self.open_center("profiles"),
        )
        self._add_menu_action(
            companion_menu,
            "历史与记忆",
            lambda: self.open_center("memory"),
        )
        self._add_menu_action(
            companion_menu,
            "预设指令",
            lambda: self.open_center("presets"),
        )
        companion_menu.addSeparator()
        self._add_menu_action(companion_menu, "番茄钟 / 计时器", self.open_timer)
        self._add_menu_action(companion_menu, "随机数", self.open_random_number)
        self._add_menu_action(companion_menu, "扫雷", self.open_minesweeper)
        companion_menu.addSeparator()
        self._add_menu_action(
            companion_menu,
            "API 与隐私设置",
            lambda: self.open_center("api"),
        )

    @staticmethod
    def _add_menu_action(menu: QMenu, label: str, callback: Any) -> QAction:
        action = QAction(label, menu)
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def open_center(self, tab: str = "chat") -> CompanionCenterDialog:
        """显示统一陪伴中心并切换到指定页面。"""

        self._seed_default_presets()
        if self._center is None:
            self._center = CompanionCenterDialog(self)
        self._center.load_pet(self.pet_id())
        self._center.select_tab(tab)
        self._center.show()
        self._center.raise_()
        self._center.activateWindow()
        return self._center

    def open_compact_chat(self) -> CompanionCenterDialog:
        """显示只含聊天页的紧凑窗口；打开本身不会发起网络请求。"""

        self._seed_default_presets()
        if self._compact_chat is None:
            self._compact_chat = CompanionCenterDialog(self, compact_chat=True)
        self._compact_chat.load_pet(self.pet_id())
        self._compact_chat.select_tab("chat")
        self._compact_chat.show()
        self._compact_chat.raise_()
        self._compact_chat.activateWindow()
        return self._compact_chat

    def open_timer(self) -> CompanionTimerDialog:
        """显示可视化番茄钟、倒计时和正计时窗口。"""

        if self._timer_dialog is None:
            self._timer_dialog = CompanionTimerDialog(self)
        self._timer_dialog.show()
        self._timer_dialog.raise_()
        self._timer_dialog.activateWindow()
        return self._timer_dialog

    def open_minesweeper(self) -> MinesweeperDialog:
        """显示独立扫雷窗口。"""

        if self._minesweeper_dialog is None:
            self._minesweeper_dialog = MinesweeperDialog(self)
        self._minesweeper_dialog.show()
        self._minesweeper_dialog.raise_()
        self._minesweeper_dialog.activateWindow()
        return self._minesweeper_dialog

    def open_random_number(self) -> int | None:
        """询问安全整数范围并把本地随机结果显示在现有气泡。"""

        minimum, accepted = QInputDialog.getInt(
            self.window,
            "范围随机数",
            "最小值：",
            1,
            -1_000_000_000,
            1_000_000_000,
        )
        if not accepted:
            return None
        maximum, accepted = QInputDialog.getInt(
            self.window,
            "范围随机数",
            "最大值：",
            max(minimum, 100),
            -1_000_000_000,
            1_000_000_000,
        )
        if not accepted:
            return None
        try:
            result = random_integer(minimum, maximum)
        except ValueError as exc:
            QMessageBox.warning(self.window, "范围随机数", str(exc))
            return None
        self.window.show_companion_text(f"这次抽到：{result}")
        self.window.trigger_companion_action("curious")
        return result

    def set_hourly_enabled(self, enabled: bool) -> None:
        """保存整点报时开关并重建去重调度器。"""

        self.storage.set_tool_settings(
            self.pet_id(),
            "hourly_chime",
            {"grace_seconds": 90},
            enabled=bool(enabled),
        )
        self._hourly_scheduler = HourlyChimeScheduler(grace_seconds=90)

    def hourly_enabled(self) -> bool:
        """返回当前宠物是否启用整点报时。"""

        settings = self.storage.get_tool_settings(self.pet_id(), "hourly_chime")
        return bool(settings and settings["enabled"])

    @Slot()
    def _hourly_tick(self) -> None:
        """轮询整点事件；关闭时不生成消息。"""

        if not self.hourly_enabled():
            return
        event = self._hourly_scheduler.poll(datetime.now())
        if event is None:
            return
        hour = int(getattr(event, "hour", datetime.now().hour))
        self.window.show_companion_text(f"现在是 {hour:02d}:00，记得活动一下～")
        self.window.trigger_companion_action("wave")

    @Slot()
    def on_pet_changed(self) -> None:
        """在 PetWindow 切换形象后取消旧请求并加载新宠物数据。"""

        self._seed_default_presets()
        self._hourly_scheduler = HourlyChimeScheduler(grace_seconds=90)
        if self._center is not None:
            self._center.load_pet(self.pet_id())
        if self._compact_chat is not None:
            self._compact_chat.load_pet(self.pet_id())

    def shutdown(self) -> None:
        """停止计时器和聊天线程并关闭陪伴层窗口。"""

        self._hourly_timer.stop()
        for dialog in (
            self._center,
            self._compact_chat,
            self._timer_dialog,
            self._minesweeper_dialog,
        ):
            if dialog is not None:
                if isinstance(dialog, CompanionCenterDialog):
                    dialog.cancel_chat()
                dialog.close()


class CompanionCenterDialog(QDialog):
    """统一管理聊天、档案、记忆、历史、预设和 API 设置。"""

    _TAB_KEYS = ("chat", "profiles", "memory", "history", "presets", "tools", "api")

    def __init__(
        self,
        controller: CompanionController,
        *,
        compact_chat: bool = False,
    ) -> None:
        super().__init__(controller.window)
        self.controller = controller
        self._compact_chat = bool(compact_chat)
        self.setWindowTitle(
            "OnePic 快捷聊天" if self._compact_chat else "OnePic 智能陪伴中心"
        )
        if self._compact_chat:
            self.setMinimumSize(440, 500)
            self.resize(480, 560)
        else:
            self.setMinimumSize(760, 620)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._loaded_pet_id = controller.pet_id()
        self._chat_request = QtChatRequest(parent=self)
        self._chat_request.delta.connect(self._chat_delta)
        self._chat_request.succeeded.connect(self._chat_succeeded)
        self._chat_request.failed.connect(self._chat_failed)
        self._chat_request.finished.connect(self._chat_finished)
        self._pending_user_text = ""
        self._pending_pet_id: str | None = None
        self._stream_buffer = ""

        layout = QVBoxLayout(self)
        self.privacy_notice = QLabel(
            "档案、历史和记忆保存在本机；云聊仅在你启用并发送时传输"
            "当前消息、角色/委托人档案、只读好感度、勾选的记忆、所选本次"
            "预设文本（如有）及内置情绪/动作白名单。聊天历史默认不发送，"
            "只有你显式开启时才携带最近 30 条；绝不上传角色图片。"
        )
        self.privacy_notice.setWordWrap(True)
        layout.addWidget(self.privacy_notice)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_chat_tab(), "聊天")
        self.tabs.addTab(self._build_profiles_tab(), "档案")
        self.tabs.addTab(self._build_memory_tab(), "记忆")
        self.tabs.addTab(self._build_history_tab(), "历史")
        self.tabs.addTab(self._build_presets_tab(), "预设")
        self.tabs.addTab(self._build_tools_tab(), "工具")
        self.tabs.addTab(self._build_api_tab(), "API / 隐私")
        if self._compact_chat:
            self.privacy_notice.setText(
                "聊天记录保存在本机；只有你配置服务并点击发送后才会联网，"
                "打开此窗口不会联网，也不会上传角色图片。"
            )
            while self.tabs.count() > 1:
                self.tabs.removeTab(self.tabs.count() - 1)

    @property
    def pet_id(self) -> str:
        """返回当前已加载的快照标识，不跟随窗口属性瞬时漂移。"""

        return self._loaded_pet_id

    def load_pet(self, pet_id: str) -> None:
        """切换陪伴中心数据快照，在途请求仍绑定原宠物。"""

        normalized = normalize_pet_id(pet_id)
        if normalized != self._loaded_pet_id:
            self.cancel_chat()
            self._loaded_pet_id = normalized
        self.refresh_all()

    def select_tab(self, key: str) -> None:
        """按稳定键切换页面，未知键回退到聊天页。"""

        if self._compact_chat:
            self.tabs.setCurrentIndex(0)
            return
        try:
            index = self._TAB_KEYS.index(key)
        except ValueError:
            index = 0
        self.tabs.setCurrentIndex(index)

    def _build_chat_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.chat_output = QPlainTextEdit(tab)
        self.chat_output.setReadOnly(True)
        self.chat_output.setPlaceholderText("聊天历史会按当前宠物隔离显示。")
        layout.addWidget(self.chat_output, 1)
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("本次预设：", tab))
        self.chat_preset = QComboBox(tab)
        preset_row.addWidget(self.chat_preset, 1)
        layout.addLayout(preset_row)
        self.chat_input = QPlainTextEdit(tab)
        self.chat_input.setPlaceholderText("输入消息；只有点击发送后才可能联网。")
        self.chat_input.setMaximumHeight(96)
        layout.addWidget(self.chat_input)
        button_row = QHBoxLayout()
        self.chat_status = QLabel("云聊默认关闭", tab)
        button_row.addWidget(self.chat_status, 1)
        self.chat_cancel = QPushButton("取消", tab)
        self.chat_cancel.setEnabled(False)
        self.chat_cancel.clicked.connect(self.cancel_chat)
        button_row.addWidget(self.chat_cancel)
        self.chat_send = QPushButton("发送", tab)
        self.chat_send.clicked.connect(self.send_chat)
        button_row.addWidget(self.chat_send)
        layout.addLayout(button_row)
        return tab

    def _build_profiles_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        character_group = QGroupBox("角色人设", tab)
        character_form = QFormLayout(character_group)
        self.character_name = QLineEdit(character_group)
        self.character_persona = QPlainTextEdit(character_group)
        self.character_persona.setPlaceholderText(
            "角色身份、性格、说话方式和边界；不要粘贴密钥或私人文件路径。"
        )
        character_form.addRow("角色名：", self.character_name)
        character_form.addRow("详细人设：", self.character_persona)
        layout.addWidget(character_group)

        user_group = QGroupBox("委托人档案", tab)
        user_form = QFormLayout(user_group)
        self.user_name = QLineEdit(user_group)
        self.user_birthday = QLineEdit(user_group)
        self.user_relationship = QLineEdit(user_group)
        self.user_notes = QPlainTextEdit(user_group)
        user_form.addRow("希望被称呼：", self.user_name)
        user_form.addRow("生日：", self.user_birthday)
        user_form.addRow("与角色的关系：", self.user_relationship)
        user_form.addRow("备注：", self.user_notes)
        layout.addWidget(user_group)
        save = QPushButton("保存档案", tab)
        save.clicked.connect(self.save_profiles)
        layout.addWidget(save)
        return tab

    def _build_memory_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        notice = QLabel("只有勾选的记忆会在下一次聊天中发送给所配置的服务。", tab)
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.memory_list = QListWidget(tab)
        self.memory_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.memory_list, 1)
        row = QHBoxLayout()
        add = QPushButton("新增", tab)
        add.clicked.connect(self.add_memory)
        row.addWidget(add)
        edit = QPushButton("编辑", tab)
        edit.clicked.connect(self.edit_memory)
        row.addWidget(edit)
        delete = QPushButton("删除", tab)
        delete.clicked.connect(self.delete_memory)
        row.addWidget(delete)
        row.addStretch(1)
        layout.addLayout(row)
        return tab

    def _build_history_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.history_output = QPlainTextEdit(tab)
        self.history_output.setReadOnly(True)
        layout.addWidget(self.history_output, 1)
        row = QHBoxLayout()
        refresh = QPushButton("刷新", tab)
        refresh.clicked.connect(self.refresh_history)
        row.addWidget(refresh)
        clear = QPushButton("清空当前宠物历史", tab)
        clear.clicked.connect(self.clear_history)
        row.addWidget(clear)
        row.addStretch(1)
        layout.addLayout(row)
        return tab

    def _build_presets_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.preset_table = QTableWidget(0, 2, tab)
        self.preset_table.setHorizontalHeaderLabels(("显示名", "文本提示词"))
        self.preset_table.horizontalHeader().setStretchLastSection(True)
        self.preset_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        layout.addWidget(self.preset_table, 1)
        row = QHBoxLayout()
        add = QPushButton("新增", tab)
        add.clicked.connect(self.add_preset_row)
        row.addWidget(add)
        delete = QPushButton("删除选中", tab)
        delete.clicked.connect(self.delete_selected_preset)
        row.addWidget(delete)
        save = QPushButton("保存全部", tab)
        save.clicked.connect(self.save_presets)
        row.addWidget(save)
        row.addStretch(1)
        layout.addLayout(row)
        return tab

    def _build_tools_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.hourly_chime = QCheckBox("启用整点报时（纯本地）", tab)
        self.hourly_chime.toggled.connect(self.controller.set_hourly_enabled)
        layout.addWidget(self.hourly_chime)
        timer = QPushButton("打开番茄钟 / 正倒计时", tab)
        timer.clicked.connect(self.controller.open_timer)
        layout.addWidget(timer)
        random_button = QPushButton("生成范围随机数", tab)
        random_button.clicked.connect(self.controller.open_random_number)
        layout.addWidget(random_button)
        mine = QPushButton("打开扫雷", tab)
        mine.clicked.connect(self.controller.open_minesweeper)
        layout.addWidget(mine)
        layout.addStretch(1)
        return tab

    def _build_api_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        notice = QLabel(
            "云聊默认关闭。共享 API Key 不能安全地内置在 EXE 中；请使用自己的密钥，"
            "或连接你已经运行的本机 OpenAI-compatible 服务。",
            tab,
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = QFormLayout()
        self.api_enabled = QCheckBox("启用云聊 / 本机模型连接", tab)
        self.api_endpoint = QLineEdit(tab)
        self.api_endpoint.setPlaceholderText(_DEFAULT_ENDPOINT)
        self.api_model = QLineEdit(tab)
        self.api_model.setPlaceholderText("必须填写服务支持的模型名")
        self.api_key = QLineEdit(tab)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("留空表示不修改已安全保存的密钥")
        self.api_stream = QCheckBox("流式显示回复", tab)
        self.api_stream.setChecked(True)
        self.api_include_history = QCheckBox(
            "发送最近 30 条聊天历史（默认关闭）",
            tab,
        )
        self.api_timeout = QSpinBox(tab)
        self.api_timeout.setRange(5, 600)
        self.api_timeout.setValue(60)
        self.api_key_status = QLabel("未检查", tab)
        form.addRow("状态：", self.api_enabled)
        form.addRow("服务地址：", self.api_endpoint)
        form.addRow("模型：", self.api_model)
        form.addRow("API Key：", self.api_key)
        form.addRow("密钥状态：", self.api_key_status)
        form.addRow("回复方式：", self.api_stream)
        form.addRow("上下文：", self.api_include_history)
        form.addRow("超时（秒）：", self.api_timeout)
        layout.addLayout(form)
        row = QHBoxLayout()
        save = QPushButton("安全保存设置", tab)
        save.clicked.connect(self.save_api_settings)
        row.addWidget(save)
        remove = QPushButton("删除已保存密钥", tab)
        remove.clicked.connect(self.delete_api_key)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        data_group = QGroupBox("本地数据控制", tab)
        data_layout = QVBoxLayout(data_group)
        data_notice = QLabel(
            "可导出或删除当前宠物的档案、历史、记忆、预设和工具设置；"
            "导出文件不含 API Key。",
            data_group,
        )
        data_notice.setWordWrap(True)
        data_layout.addWidget(data_notice)
        data_row = QHBoxLayout()
        export = QPushButton("导出当前宠物数据…", data_group)
        export.clicked.connect(self.export_local_data)
        data_row.addWidget(export)
        delete_data = QPushButton("删除当前宠物全部陪伴数据", data_group)
        delete_data.clicked.connect(self.delete_local_data)
        data_row.addWidget(delete_data)
        data_row.addStretch(1)
        data_layout.addLayout(data_row)
        layout.addWidget(data_group)
        layout.addStretch(1)
        return tab

    def refresh_all(self) -> None:
        """重新加载当前宠物的全部本地状态。"""

        self.refresh_profiles()
        self.refresh_memories()
        self.refresh_history()
        self.refresh_presets()
        self.refresh_api_settings()
        blocked = self.hourly_chime.blockSignals(True)
        self.hourly_chime.setChecked(self.controller.hourly_enabled())
        self.hourly_chime.blockSignals(blocked)

    def refresh_profiles(self) -> None:
        character = self.controller.storage.get_character_profile(self.pet_id) or {}
        user = self.controller.storage.get_user_profile(self.pet_id) or {}
        self.character_name.setText(str(character.get("name", "")))
        self.character_persona.setPlainText(str(character.get("persona", "")))
        self.user_name.setText(str(user.get("preferred_name", "")))
        self.user_birthday.setText(str(user.get("birthday", "")))
        self.user_relationship.setText(str(user.get("relationship", "")))
        self.user_notes.setPlainText(str(user.get("notes", "")))

    @Slot()
    def save_profiles(self) -> None:
        self.controller.storage.set_character_profile(
            self.pet_id,
            {
                "name": self.character_name.text().strip(),
                "persona": self.character_persona.toPlainText().strip(),
            },
        )
        self.controller.storage.set_user_profile(
            self.pet_id,
            {
                "preferred_name": self.user_name.text().strip(),
                "birthday": self.user_birthday.text().strip(),
                "relationship": self.user_relationship.text().strip(),
                "notes": self.user_notes.toPlainText().strip(),
            },
        )
        self.controller.window.show_companion_text("档案已经安全保存在本机。")

    def refresh_memories(self) -> None:
        self.memory_list.clear()
        for memory in self.controller.storage.list_memories(self.pet_id):
            title = memory["title"] or memory["content"][:28]
            item = QListWidgetItem(str(title), self.memory_list)
            item.setData(Qt.ItemDataRole.UserRole, memory["memory_id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip(str(memory["content"]))

    def _selected_memory(self) -> dict[str, object] | None:
        item = self.memory_list.currentItem()
        if item is None:
            return None
        memory_id = str(item.data(Qt.ItemDataRole.UserRole))
        return self.controller.storage.get_memory(self.pet_id, memory_id)

    @Slot()
    def add_memory(self) -> None:
        title, accepted = QInputDialog.getText(self, "新增记忆", "标题（可选）：")
        if not accepted:
            return
        content, accepted = QInputDialog.getMultiLineText(
            self,
            "新增记忆",
            "记忆内容：",
        )
        if not accepted or not content.strip():
            return
        self.controller.storage.save_memory(
            self.pet_id,
            content,
            title=title,
        )
        self.refresh_memories()

    @Slot()
    def edit_memory(self) -> None:
        memory = self._selected_memory()
        if memory is None:
            return
        title, accepted = QInputDialog.getText(
            self,
            "编辑记忆",
            "标题（可选）：",
            text=str(memory["title"]),
        )
        if not accepted:
            return
        content, accepted = QInputDialog.getMultiLineText(
            self,
            "编辑记忆",
            "记忆内容：",
            str(memory["content"]),
        )
        if not accepted or not content.strip():
            return
        self.controller.storage.save_memory(
            self.pet_id,
            content,
            memory_id=str(memory["memory_id"]),
            title=title,
            tags=memory.get("tags", []),
        )
        self.refresh_memories()

    @Slot()
    def delete_memory(self) -> None:
        memory = self._selected_memory()
        if memory is None:
            return
        answer = QMessageBox.question(
            self,
            "删除记忆",
            "只删除当前选中的本地记忆？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.controller.storage.delete_memory(
            self.pet_id,
            str(memory["memory_id"]),
        )
        self.refresh_memories()

    def selected_memories(self) -> list[dict[str, object]]:
        """返回用户明确勾选的记忆；未勾选内容绝不进入请求。"""

        selected: list[dict[str, object]] = []
        for index in range(self.memory_list.count()):
            item = self.memory_list.item(index)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            memory = self.controller.storage.get_memory(
                self.pet_id,
                str(item.data(Qt.ItemDataRole.UserRole)),
            )
            if memory is not None:
                selected.append(memory)
        return selected

    def refresh_history(self) -> None:
        messages = self.controller.storage.list_messages(self.pet_id, limit=500)
        rendered = []
        for message in messages:
            role = "你" if message["role"] == "user" else "桌宠"
            rendered.append(f"[{message['created_at']}] {role}：\n{message['content']}")
        text = "\n\n".join(rendered)
        self.history_output.setPlainText(text)
        self.chat_output.setPlainText(text)

    @Slot()
    def clear_history(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空历史",
            "将永久清空当前宠物的本地聊天历史；档案和记忆不受影响。继续吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.controller.storage.clear_messages(self.pet_id)
        self.refresh_history()

    def refresh_presets(self) -> None:
        commands = self.controller.storage.list_preset_commands(self.pet_id)
        self.preset_table.setRowCount(0)
        self.chat_preset.clear()
        self.chat_preset.addItem("无", None)
        for command in commands:
            row = self.preset_table.rowCount()
            self.preset_table.insertRow(row)
            label_item = QTableWidgetItem(str(command["label"]))
            label_item.setData(Qt.ItemDataRole.UserRole, command["command_id"])
            prompt_item = QTableWidgetItem(str(command["prompt"]))
            self.preset_table.setItem(row, 0, label_item)
            self.preset_table.setItem(row, 1, prompt_item)
            if command["enabled"]:
                self.chat_preset.addItem(str(command["label"]), str(command["prompt"]))

    @Slot()
    def add_preset_row(self) -> None:
        row = self.preset_table.rowCount()
        self.preset_table.insertRow(row)
        label = QTableWidgetItem("新指令")
        label.setData(Qt.ItemDataRole.UserRole, f"custom-{uuid.uuid4().hex[:12]}")
        self.preset_table.setItem(row, 0, label)
        self.preset_table.setItem(row, 1, QTableWidgetItem(""))

    @Slot()
    def delete_selected_preset(self) -> None:
        row = self.preset_table.currentRow()
        if row < 0:
            return
        item = self.preset_table.item(row, 0)
        if item is not None:
            command_id = item.data(Qt.ItemDataRole.UserRole)
            if command_id:
                self.controller.storage.delete_preset_command(
                    self.pet_id,
                    str(command_id),
                )
        self.preset_table.removeRow(row)
        self.refresh_presets()

    @Slot()
    def save_presets(self) -> None:
        for row in range(self.preset_table.rowCount()):
            label_item = self.preset_table.item(row, 0)
            prompt_item = self.preset_table.item(row, 1)
            label = label_item.text().strip() if label_item else ""
            prompt = prompt_item.text().strip() if prompt_item else ""
            if not label or not prompt:
                continue
            command_id = label_item.data(Qt.ItemDataRole.UserRole)
            if not command_id:
                command_id = f"custom-{uuid.uuid4().hex[:12]}"
                label_item.setData(Qt.ItemDataRole.UserRole, command_id)
            self.controller.storage.save_preset_command(
                self.pet_id,
                str(command_id),
                label,
                prompt,
                sort_order=row,
            )
        self.refresh_presets()
        self.controller.window.show_companion_text("预设指令已经保存在本机。")

    def refresh_api_settings(self) -> None:
        record = self.controller.storage.get_tool_settings(self.pet_id, "ai_chat")
        settings = record["settings"] if record else {}
        self.api_enabled.setChecked(bool(record and record["enabled"]))
        self.api_endpoint.setText(str(settings.get("endpoint", _DEFAULT_ENDPOINT)))
        self.api_model.setText(str(settings.get("model", _DEFAULT_MODEL)))
        self.api_stream.setChecked(bool(settings.get("stream", True)))
        self.api_include_history.setChecked(
            bool(settings.get("include_history", False))
        )
        self.api_timeout.setValue(int(settings.get("timeout", 60)))
        self.api_key.clear()
        try:
            endpoint = resolve_chat_completions_url(
                str(settings.get("endpoint", _DEFAULT_ENDPOINT))
            )
            key_exists = self.controller.key_store_for(
                self.pet_id,
                endpoint,
            ).exists()
        except ValueError:
            key_exists = False
        self.api_key_status.setText(
            "已为当前宠物与服务使用 Windows DPAPI 保存"
            if key_exists
            else "当前宠物与服务未保存"
        )
        self.chat_status.setText(
            "已启用；发送时才联网" if self.api_enabled.isChecked() else "云聊默认关闭"
        )

    @Slot()
    def save_api_settings(self) -> None:
        endpoint_text = self.api_endpoint.text().strip() or _DEFAULT_ENDPOINT
        model = self.api_model.text().strip()
        try:
            endpoint = resolve_chat_completions_url(endpoint_text)
        except ValueError as exc:
            QMessageBox.warning(self, "API 设置", str(exc))
            return
        if _endpoint_contains_secret_query(endpoint):
            QMessageBox.warning(
                self,
                "API 设置",
                "服务地址不能携带 key、token 或 secret 等敏感查询参数；"
                "请把密钥单独填入 API Key 栏。",
            )
            return
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme != "https" and not _is_loopback_endpoint(endpoint):
            answer = QMessageBox.question(
                self,
                "不安全的服务地址",
                "该地址使用普通 HTTP 且不是本机服务，消息和密钥可能被窃听。仍要保存吗？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if self.api_enabled.isChecked() and not model:
            QMessageBox.warning(self, "API 设置", "启用聊天前必须填写模型名。")
            return
        key_text = self.api_key.text().strip()
        try:
            key_store = self.controller.key_store_for(self.pet_id, endpoint)
        except ValueError as exc:
            QMessageBox.warning(self, "API 设置", str(exc))
            return
        if key_text:
            try:
                key_store.save(key_text)
            except (SecretStorageError, ValueError) as exc:
                QMessageBox.warning(self, "API 设置", str(exc))
                return
        if (
            self.api_enabled.isChecked()
            and not _is_loopback_endpoint(endpoint)
            and not key_store.exists()
        ):
            QMessageBox.warning(self, "API 设置", "云端服务需要先安全保存 API Key。")
            return
        self.controller.storage.set_tool_settings(
            self.pet_id,
            "ai_chat",
            {
                "endpoint": endpoint,
                "model": model,
                "stream": self.api_stream.isChecked(),
                "include_history": self.api_include_history.isChecked(),
                "timeout": self.api_timeout.value(),
            },
            enabled=self.api_enabled.isChecked(),
        )
        self.refresh_api_settings()
        self.controller.window.show_companion_text("API 设置已保存；密钥未写入数据库。")

    @Slot()
    def delete_api_key(self) -> None:
        endpoint_text = self.api_endpoint.text().strip() or _DEFAULT_ENDPOINT
        try:
            endpoint = resolve_chat_completions_url(endpoint_text)
            key_store = self.controller.key_store_for(self.pet_id, endpoint)
        except ValueError as exc:
            QMessageBox.warning(self, "删除 API Key", str(exc))
            return
        answer = QMessageBox.question(
            self,
            "删除 API Key",
            "删除当前宠物与当前服务地址绑定的 API Key？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            key_store.delete()
        except SecretStorageError as exc:
            QMessageBox.warning(self, "删除 API Key", str(exc))
            return
        self.refresh_api_settings()

    @Slot()
    def export_local_data(self) -> None:
        """将当前宠物的本地数据导出到用户选择的 JSON。"""

        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出陪伴数据",
            f"onepic-companion-{self.pet_id}.json",
            "JSON (*.json)",
        )
        if not filename:
            return
        target = Path(filename)
        try:
            target.write_text(
                json.dumps(
                    self.controller.storage.export_pet_data(self.pet_id),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.warning(self, "导出陪伴数据", f"写入失败：{exc}")
            return
        QMessageBox.information(self, "导出陪伴数据", "导出完成，文件不含 API Key。")

    @Slot()
    def delete_local_data(self) -> None:
        """经二次确认删除当前宠物的陪伴数据。"""

        answer = QMessageBox.warning(
            self,
            "删除全部陪伴数据",
            "此操作将永久删除当前宠物的档案、历史、记忆、预设和工具设置。\n"
            "API Key 不会随之删除，可使用上方单独按钮删除。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.cancel_chat()
        self.controller.storage.delete_pet_data(self.pet_id)
        self.controller._seed_default_presets(self.pet_id)
        self.refresh_all()
        self.controller.window.show_companion_text("当前宠物的陪伴数据已删除。")

    @Slot()
    def send_chat(self) -> None:
        """收集用户明确选择的数据并启动后台聊天请求。"""

        if self._chat_request.active:
            return
        user_text = self.chat_input.toPlainText().strip()
        if not user_text:
            return
        record = self.controller.storage.get_tool_settings(self.pet_id, "ai_chat")
        if not record or not record["enabled"]:
            QMessageBox.information(self, "实时聊天", "请先在“API / 隐私”中启用聊天。")
            if self._compact_chat:
                self.controller.open_center("api")
            else:
                self.select_tab("api")
            return
        request_pet_id = self.pet_id
        settings = record["settings"]
        try:
            endpoint = resolve_chat_completions_url(
                str(settings.get("endpoint", ""))
            )
        except ValueError as exc:
            QMessageBox.warning(self, "实时聊天", str(exc))
            return
        if _endpoint_contains_secret_query(endpoint):
            QMessageBox.warning(
                self,
                "实时聊天",
                "已保存的服务地址含敏感查询参数，请在 API / 隐私中修正后再发送。",
            )
            return
        api_key: str | None = None
        try:
            key_store = self.controller.key_store_for(request_pet_id, endpoint)
        except ValueError as exc:
            QMessageBox.warning(self, "实时聊天", str(exc))
            return
        if key_store.exists():
            try:
                api_key = key_store.load()
            except SecretStorageError as exc:
                QMessageBox.warning(self, "实时聊天", str(exc))
                return
        character = self.controller.storage.get_character_profile(request_pet_id) or {
            "name": "桌宠",
            "persona": "温和、诚实，不虚构共同经历。",
        }
        user_profile = self.controller.storage.get_user_profile(request_pet_id)
        history = self.history_for_request(settings, pet_id=request_pet_id)
        preset_prompt = self.chat_preset.currentData()
        messages = build_messages(
            user_text,
            history=history,
            character_profile=character,
            user_profile=user_profile,
            selected_memories=self.selected_memories(),
            affection=self.controller.bond(),
            preset_prompt=str(preset_prompt) if preset_prompt else None,
            allowed_emotions=_BASE_EMOTIONS,
            allowed_actions=self.controller.allowed_actions(),
        )
        request: dict[str, object] = {
            "endpoint": endpoint,
            "api_key": api_key,
            "model": str(settings.get("model", "")),
            "messages": messages,
            "stream": bool(settings.get("stream", True)),
            "timeout": float(settings.get("timeout", 60)),
            "allowed_emotions": _BASE_EMOTIONS,
            "allowed_actions": self.controller.allowed_actions(),
        }
        self._pending_user_text = user_text
        self._pending_pet_id = request_pet_id
        self._stream_buffer = ""
        self.chat_input.clear()
        self.chat_output.appendPlainText(f"\n你：\n{user_text}\n")
        self.chat_status.setText("正在等待回复…")
        self.chat_send.setEnabled(False)
        self.chat_cancel.setEnabled(True)

        try:
            self._chat_request.start(**request)
        except (TypeError, ValueError, RuntimeError) as exc:
            self._chat_failed(str(exc))
            self._chat_finished()

    @Slot(str)
    def _chat_delta(self, delta: str) -> None:
        self._stream_buffer += delta
        preview = self._stream_buffer[-120:].replace("\n", " ")
        self.chat_status.setText(f"接收中：{preview}")

    @Slot(object)
    def _chat_succeeded(self, value: object) -> None:
        reply = value if isinstance(value, ChatReply) else ChatReply(str(value))
        visible_text = reply.text.strip() or "（桌宠做了一个动作）"
        pending_pet_id = self._pending_pet_id
        if pending_pet_id is None:
            return
        self.controller.storage.add_message(
            pending_pet_id,
            "user",
            self._pending_user_text,
        )
        self.controller.storage.add_message(
            pending_pet_id,
            "assistant",
            visible_text,
        )
        if (
            pending_pet_id != self.pet_id
            or pending_pet_id != self.controller.pet_id()
        ):
            return
        self.chat_output.appendPlainText(f"桌宠：\n{visible_text}\n")
        self.history_output.setPlainText(self.chat_output.toPlainText())
        self.controller.window.show_companion_text(visible_text[:220])
        if reply.hint is not None:
            action = reply.hint.action or reply.hint.emotion
            if action:
                self.controller.window.trigger_companion_action(action)
        self.chat_status.setText("回复完成")

    @Slot(str)
    def _chat_failed(self, message: str) -> None:
        if (
            self._pending_pet_id is not None
            and self._pending_pet_id != self.pet_id
        ):
            return
        if message == "聊天请求已取消":
            self.chat_status.setText("已取消；未写入历史")
            return
        self.chat_output.appendPlainText(f"聊天失败：{message}\n")
        self.chat_status.setText("聊天失败；未写入历史")

    @Slot()
    def _chat_finished(self) -> None:
        self._pending_user_text = ""
        self._pending_pet_id = None
        self.chat_send.setEnabled(True)
        self.chat_cancel.setEnabled(False)

    @Slot()
    def cancel_chat(self) -> None:
        if not self._chat_request.active:
            return
        self.chat_status.setText("正在取消…")
        self._chat_request.cancel()

    def history_for_request(
        self,
        settings: object,
        *,
        pet_id: str | None = None,
    ) -> list[dict[str, object]]:
        """只在用户显式开启后返回最近历史快照。"""

        if not isinstance(settings, dict) or not bool(
            settings.get("include_history", False)
        ):
            return []
        target_pet_id = normalize_pet_id(pet_id or self.pet_id)
        return self.controller.storage.list_messages(target_pet_id, limit=30)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.cancel_chat()
        super().closeEvent(event)


def _format_seconds(seconds: float) -> str:
    """将秒数稳定格式化为计时器文本。"""

    whole = max(0, int(seconds + 0.999))
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class CompanionTimerDialog(QDialog):
    """提供番茄钟、倒计时与正计时的非模态窗口。"""

    _PHASE_LABELS = {
        PomodoroPhase.FOCUS: "专注",
        PomodoroPhase.SHORT_BREAK: "短休息",
        PomodoroPhase.LONG_BREAK: "长休息",
    }

    def __init__(self, controller: CompanionController) -> None:
        super().__init__(controller.window)
        self.controller = controller
        self.setWindowTitle("番茄钟与计时器 · OnePic")
        self.setMinimumWidth(360)

        self._pomodoro = PomodoroState(PomodoroConfig())
        self._countdown = CountdownTimer(5 * 60)
        self._countup = CountUpTimer()

        root = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs)
        self._build_pomodoro_tab()
        self._build_countdown_tab()
        self._build_countup_tab()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start()
        self._refresh()

    @staticmethod
    def _button_row(*buttons: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        for button in buttons:
            row.addWidget(button)
        return row

    def _build_pomodoro_tab(self) -> None:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.pomodoro_phase = QLabel(tab)
        self.pomodoro_phase.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pomodoro_clock = QLabel(tab)
        self.pomodoro_clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pomodoro_clock.setStyleSheet("font-size: 34px; font-weight: 600;")
        self.pomodoro_progress = QLabel(tab)
        self.pomodoro_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)

        start = QPushButton("开始 / 继续", tab)
        pause = QPushButton("暂停", tab)
        reset = QPushButton("重置当前阶段", tab)
        reset_all = QPushButton("重置全部", tab)
        start.clicked.connect(self._start_pomodoro)
        pause.clicked.connect(self._pause_pomodoro)
        reset.clicked.connect(self._reset_pomodoro)
        reset_all.clicked.connect(self._reset_all_pomodoro)

        layout.addStretch(1)
        layout.addWidget(self.pomodoro_phase)
        layout.addWidget(self.pomodoro_clock)
        layout.addWidget(self.pomodoro_progress)
        layout.addLayout(self._button_row(start, pause))
        layout.addLayout(self._button_row(reset, reset_all))
        layout.addStretch(1)
        self.tabs.addTab(tab, "番茄钟")

    def _build_countdown_tab(self) -> None:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.countdown_minutes = QSpinBox(tab)
        self.countdown_minutes.setRange(1, 24 * 60)
        self.countdown_minutes.setValue(5)
        self.countdown_minutes.setSuffix(" 分钟")
        form.addRow("设定：", self.countdown_minutes)
        self.countdown_clock = QLabel(tab)
        self.countdown_clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countdown_clock.setStyleSheet("font-size: 34px; font-weight: 600;")
        start = QPushButton("开始 / 继续", tab)
        pause = QPushButton("暂停", tab)
        reset = QPushButton("按设定重置", tab)
        start.clicked.connect(self._start_countdown)
        pause.clicked.connect(self._pause_countdown)
        reset.clicked.connect(self._reset_countdown)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(self.countdown_clock)
        layout.addLayout(self._button_row(start, pause, reset))
        layout.addStretch(1)
        self.tabs.addTab(tab, "倒计时")

    def _build_countup_tab(self) -> None:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.countup_clock = QLabel(tab)
        self.countup_clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countup_clock.setStyleSheet("font-size: 34px; font-weight: 600;")
        start = QPushButton("开始 / 继续", tab)
        pause = QPushButton("暂停", tab)
        reset = QPushButton("清零", tab)
        start.clicked.connect(self._start_countup)
        pause.clicked.connect(self._pause_countup)
        reset.clicked.connect(self._reset_countup)
        layout.addStretch(1)
        layout.addWidget(self.countup_clock)
        layout.addLayout(self._button_row(start, pause, reset))
        layout.addStretch(1)
        self.tabs.addTab(tab, "正计时")

    @Slot()
    def _start_pomodoro(self) -> None:
        self._pomodoro.start(time.monotonic())
        self._refresh()

    @Slot()
    def _pause_pomodoro(self) -> None:
        self._pomodoro.pause(time.monotonic())
        self._refresh()

    @Slot()
    def _reset_pomodoro(self) -> None:
        self._pomodoro.reset_current()
        self._refresh()

    @Slot()
    def _reset_all_pomodoro(self) -> None:
        self._pomodoro.reset_all()
        self._refresh()

    @Slot()
    def _start_countdown(self) -> None:
        if self._countdown.finished:
            self._countdown.reset(self.countdown_minutes.value() * 60)
        self._countdown.start(time.monotonic())
        self._refresh()

    @Slot()
    def _pause_countdown(self) -> None:
        self._countdown.pause(time.monotonic())
        self._refresh()

    @Slot()
    def _reset_countdown(self) -> None:
        self._countdown.reset(self.countdown_minutes.value() * 60)
        self._refresh()

    @Slot()
    def _start_countup(self) -> None:
        self._countup.start(time.monotonic())
        self._refresh()

    @Slot()
    def _pause_countup(self) -> None:
        self._countup.pause(time.monotonic())
        self._refresh()

    @Slot()
    def _reset_countup(self) -> None:
        self._countup.reset()
        self._refresh()

    @Slot()
    def _refresh(self) -> None:
        now = time.monotonic()
        transition = self._pomodoro.sync(now)
        if transition is not None:
            if transition.previous_phase is PomodoroPhase.FOCUS:
                self.controller.window.show_companion_text(
                    "专注完成，起来活动一下吧。"
                )
                self.controller.window.trigger_companion_action("happy")
            else:
                self.controller.window.show_companion_text("休息结束，准备好再专注一轮了吗？")
                self.controller.window.trigger_companion_action("wave")

        if self._countdown.sync(now):
            self.controller.window.show_companion_text("倒计时结束了。")
            self.controller.window.trigger_companion_action("wave")

        pomodoro = self._pomodoro.snapshot(now)
        phase = self._PHASE_LABELS[pomodoro.phase]
        state = "进行中" if pomodoro.timer.running else "已暂停"
        self.pomodoro_phase.setText(f"{phase} · {state}")
        self.pomodoro_clock.setText(
            _format_seconds(pomodoro.timer.remaining_seconds or 0)
        )
        self.pomodoro_progress.setText(
            f"已完成 {pomodoro.completed_focus_sessions} 轮专注 · "
            f"距长休息 {pomodoro.focus_sessions_until_long_break} 轮"
        )

        countdown = self._countdown.snapshot(now)
        self.countdown_clock.setText(
            _format_seconds(countdown.remaining_seconds or 0)
        )
        countup = self._countup.snapshot(now)
        self.countup_clock.setText(_format_seconds(countup.elapsed_seconds))


class _MineCellButton(QPushButton):
    """把鼠标左右键转换为带坐标的棋盘事件。"""

    activated = Signal(int, int, bool)

    def __init__(self, row: int, column: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.row = row
        self.column = column
        self.setFixedSize(30, 30)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() in {
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.RightButton,
        }:
            self.activated.emit(
                self.row,
                self.column,
                event.button() is Qt.MouseButton.RightButton,
            )
            event.accept()
            return
        super().mousePressEvent(event)


class MinesweeperDialog(QDialog):
    """使用纯逻辑模型渲染的原创扫雷窗口。"""

    _NUMBER_COLORS = {
        1: "#2563eb",
        2: "#15803d",
        3: "#dc2626",
        4: "#6d28d9",
        5: "#9f1239",
        6: "#0f766e",
        7: "#111827",
        8: "#64748b",
    }

    def __init__(self, controller: CompanionController) -> None:
        super().__init__(controller.window)
        self.controller = controller
        self.model = MinesweeperModel(rows=9, columns=9, mine_count=10)
        self._announced_status = MinesweeperStatus.READY
        self.setWindowTitle("扫雷 · OnePic")

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        self.status_label = QLabel(self)
        self.remaining_label = QLabel(self)
        restart = QPushButton("新游戏", self)
        restart.clicked.connect(self.reset_game)
        header.addWidget(self.status_label)
        header.addStretch(1)
        header.addWidget(self.remaining_label)
        header.addWidget(restart)
        root.addLayout(header)

        board_container = QWidget(self)
        grid = QGridLayout(board_container)
        grid.setSpacing(1)
        grid.setContentsMargins(0, 0, 0, 0)
        self.buttons: list[list[_MineCellButton]] = []
        for row in range(self.model.rows):
            row_buttons: list[_MineCellButton] = []
            for column in range(self.model.columns):
                button = _MineCellButton(row, column, board_container)
                button.activated.connect(self._activate_cell)
                grid.addWidget(button, row, column)
                row_buttons.append(button)
            self.buttons.append(row_buttons)
        root.addWidget(board_container, 0, Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("左键揭开，右键标记；首次揭开一定安全。", self)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hint)
        self._render()

    @Slot(int, int, bool)
    def _activate_cell(self, row: int, column: int, mark: bool) -> None:
        if mark:
            self.model.toggle_marker(row, column)
        else:
            self.model.reveal(row, column)
        self._render()
        if self.model.status is self._announced_status:
            return
        self._announced_status = self.model.status
        if self.model.status is MinesweeperStatus.WON:
            self.controller.window.show_companion_text("扫雷成功！这局判断得很稳。")
            self.controller.window.trigger_companion_action("happy")
        elif self.model.status is MinesweeperStatus.LOST:
            self.controller.window.show_companion_text("踩到雷了，没关系，再来一局。")
            self.controller.window.trigger_companion_action("surprised")

    @Slot()
    def reset_game(self) -> None:
        self.model.reset()
        self._announced_status = MinesweeperStatus.READY
        self._render()

    def _render(self) -> None:
        status_text = {
            MinesweeperStatus.READY: "等待首次揭开",
            MinesweeperStatus.PLAYING: "进行中",
            MinesweeperStatus.WON: "已胜利",
            MinesweeperStatus.LOST: "已失败",
        }[self.model.status]
        self.status_label.setText(status_text)
        self.remaining_label.setText(
            f"剩余标记：{self.model.remaining_markers}"
        )
        terminal = self.model.status in {
            MinesweeperStatus.WON,
            MinesweeperStatus.LOST,
        }
        for row in self.model.board():
            for cell in row:
                button = self.buttons[cell.row][cell.column]
                button.setEnabled(not terminal and cell.state is not CellState.REVEALED)
                button.setStyleSheet("")
                if cell.incorrect_flag:
                    button.setText("×")
                    button.setStyleSheet("color: #dc2626; font-weight: 700;")
                elif cell.contains_mine:
                    button.setText("●")
                    button.setStyleSheet("color: #dc2626; font-weight: 700;")
                elif cell.state is CellState.FLAGGED:
                    button.setText("⚑")
                    button.setStyleSheet("color: #d97706; font-weight: 700;")
                elif cell.state is CellState.REVEALED:
                    number = cell.adjacent_mines or 0
                    button.setText(str(number) if number else "")
                    color = self._NUMBER_COLORS.get(number, "#334155")
                    button.setStyleSheet(
                        f"color: {color}; font-weight: 700; background: #e5e7eb;"
                    )
                else:
                    button.setText("")
