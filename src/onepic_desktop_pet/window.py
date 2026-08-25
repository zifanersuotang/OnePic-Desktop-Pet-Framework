"""
本模块实现桌面宠物的透明窗口、连续动画、鼠标交互和自主移动。

职责范围：
- 创建无边框、透明、可选始终置顶的 QWidget；
- 播放循环或单次 PNG 序列，并支持拖拽、坐下、坐姿入睡和反向起身；
- 处理左右翻转、边缘转身停顿、亚像素时间驱动移动和同步身体起伏；
- 用窗口遮罩让人物外透明区域穿透鼠标点击；
- 拖拽、缩放按角色可见轮廓贴近屏幕边界，自主移动和追鼠标可另受用户活动区域约束，
  不受 PNG 透明留白阻挡；
- 缓存不同 DPI 下的缩放帧，并在窗口跨显示器后按新比例重新栅格化；
- 支持左键拖动、双击互动、无互动分级休息、右键尺寸菜单和可配置滚轮缩放；
- 支持头部摸动、脸部/身体/相机分区点击、连续戳击、悬停注视和拖拽后表情；
- 通过与角色素材解耦的矢量图层增强开心、害羞、惊讶、生气、困倦、疑惑、自拍和拖拽反馈；
- 支持带换向死区和近目标减速的追逐/抱住鼠标、只统计活跃度的陪打字、专注计时庆祝和角色招牌动作；
- 支持配置化多帧扩展动作、按真实动作目标去重的右键菜单、空闲随机单帧微表情、
  可切换走路样式与待机八向视线；手动动作优先播放声明的扩展序列，并保证微表情
  和视线不覆盖走路、互动或扩展序列；
- 支持纯本地动作台词池、防重复随机文字气泡和按宠物保存的用户自定义台词；
- 允许应用层挂接独立智能陪伴控制器，把档案、聊天、工具和小游戏菜单接入窗口，
  双击优先打开不自动联网的快捷聊天页；控制器不可用时安全回退到本地互动；
  其余入口只通过白名单显示文本或触发当前清单已经批准的动作；
- 允许应用层挂接休息提醒控制器，并通过不计作用户互动的专用白名单入口短暂展示
  预告、到点、完成、延期和庆祝，真实提醒状态由独立操作卡持有；
- 支持清单声明基础状态回退；被当前宠物禁用的表情不会显示旧帧、符号或旧台词；
- 优先从用户私有素材目录显示自拍成片气泡，按当前屏幕 DPI 保持清晰度，并贴近人物真实轮廓定位；
- 标准角色确认后加载本地宠物供现场验收；走路确认仍作为打包门禁；
- 维护亲密度、精力与无聊度的会话内状态；
- 可选接入与素材解耦的养成模拟、按宠物存档和基础照料交互，并优先复用已批准的版本化动作表达需求；
- 使用 QTimer 驱动状态切换及水平移动，并限制窗口不脱离当前屏幕。

Agent 快速定位：
- 窗口初始化和计时器设置位于 PetWindow.__init__()；
- 状态显示入口位于 set_state()，高 DPI 重绘位于 _refresh_pixmap()；
- 混合 Live2D 待机使用完整透明窗口遮罩，避免两套素材的画布留白差异裁掉帽子；
- 自动移动位于 _movement_tick()；
- 鼠标事件位于 mousePressEvent() 等 Qt 事件方法；
- 退出由 quit_requested 信号交给应用生命周期模块处理。

输入为 PetSettings、素材清单和可选的用户自拍照片资源，输出为可交互的 Qt 窗口。
本模块不写配置文件、不启动独立线程、不访问网络；位置持久化由 app.py 在退出时完成。
`user_assets/` 默认不进入 Git；只有用户主动放入的自拍图片才会在本机显示。
"""

from __future__ import annotations

import json
import math
import os
import random
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QContextMenuEvent,
    QCursor,
    QHideEvent,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPixmap,
    QRegion,
    QScreen,
    QShowEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QWidget,
)

from .behavior import BehaviorModel, PetMood, PetState, StateDecision
from .care import PetCareState, normalize_care_config
from .config import PetSettings
from .companion.framework import normalize_companion_ui_config
from .dialogue import (
    DialogueSelector,
    normalize_dialogue_config,
    sanitize_dialogue_lines,
)
from .emotion_effects import draw_emotion_effect, emotion_effect_name
from .input_activity import KeyboardActivityMonitor
from .profiles import PetProfile, load_pet_profile_catalog
from .resources import resource_path
from .workflow import (
    WorkflowError,
    character_is_approved,
    load_workflow,
    workflow_path,
)


DEFAULT_WALK_MOTION_FACTORS = (0.78, 0.92, 1.14, 1.16, 0.78, 0.92, 1.14, 1.16)
DISPLAY_SIZE_PRESETS = (
    ("小（180）", 180),
    ("标准（220）", 220),
    ("大（280）", 280),
    ("超大（360）", 360),
    ("真人大（450）", 450),
    ("最大（560）", 560),
)
WHEEL_RESIZE_MODES = (
    ("关闭", "disabled"),
    ("直接滚轮", "direct"),
    ("Ctrl + 滚轮（推荐）", "ctrl"),
)
WHEEL_RESIZE_STEP_PX = 10


class PetWindow(QWidget):
    """显示并控制单个桌面宠物的透明顶层窗口。"""

    quit_requested = Signal()
    pause_changed = Signal(bool)

    def __init__(self, settings: PetSettings) -> None:
        super().__init__()
        self.settings = settings
        self.behavior = BehaviorModel(settings)
        self.mood = PetMood()
        self.care = PetCareState()
        self._care_config: dict[str, object] = {"enabled": False}
        self._last_care_update = time.monotonic()
        self._last_care_action_at = 0.0
        self.state = PetState.IDLE
        self.direction = -1
        self._movement_x = float(self.x())
        self._last_movement_at = time.monotonic()
        self.paused = False
        self.dragging = False
        self._press_pending = False
        self._press_local = QPoint()
        self._press_global = QPoint()
        self._drag_offset = QPoint()
        self._wheel_resize_angle_remainder = 0
        self._wheel_resize_pixel_remainder = 0
        self._hover_zone = ""
        self._stroke_points: deque[tuple[float, QPoint]] = deque()
        self._last_stroke_reaction = 0.0
        self._poke_times: deque[float] = deque()
        self._bob_phase = False
        self._effect_phase = 0
        self._frame_index = 0
        self._animation_direction = 1
        self._animation_finished: Callable[[], None] | None = None
        self._turn_paused = False
        self._last_user_interaction = time.monotonic()
        self._sleep_after_sit = False
        self._screen_change_connected = False
        self._connected_screen: QScreen | None = None
        self._stationary = False
        self._directional_pixmaps: dict[str, QPixmap] = {}
        self._directional_ring_count = 0
        self._directional_config: dict[str, object] = {}
        self._state_fallbacks: dict[PetState, PetState] = {}
        self._touch_config: dict[str, object] = {}
        self._companion_config: dict[str, object] = {}
        self._companion_ui_config = normalize_companion_ui_config(None)
        self._break_reminder_config: object = {}
        self._dialogue_config = normalize_dialogue_config({"enabled": False})
        self._dialogue_pet_id = "default"
        self._dialogue_selector = DialogueSelector(self._dialogue_config)
        self._extension_animations: dict[str, list[QPixmap]] = {}
        self._extension_animation_config: dict[str, dict[str, object]] = {}
        self._extension_active_name: str | None = None
        self._extension_frame_index = 0
        self._extension_finished: Callable[[], None] | None = None
        self._extension_loop_override: bool | None = None
        self._extension_single_frame = False
        self._autoplay_recent: deque[str] = deque()
        self._autoplay_active_sequence: str | None = None
        self._autoplay_cycles_remaining = 0
        self._auto_expression_recent: deque[int] = deque()
        self._walk_variant_pixmaps: dict[str, list[QPixmap]] = {}
        self._walk_variant_labels: dict[str, str] = {}
        self._walk_variant_mirror: dict[str, bool] = {}
        self._front_walk_mirror_direction = "left"
        self._active_walk_variant = "front"
        self._hybrid_runtime_config: dict[str, object] = {}
        self._live2d_view = None
        self._live2d_ready = False
        self._touch_transition_pixmaps: list[QPixmap] = []
        self._touch_override_pixmap: QPixmap | None = None
        self._touch_steps: deque[tuple[str, object, int]] = deque()
        self._touch_active = False
        self._last_touch_branch: PetState | None = None
        self._last_touch_started_at = 0.0
        self._cursor_chase_enabled = False
        self._cursor_hugging = False
        self._keyboard_monitor = KeyboardActivityMonitor()
        self._typing_press_times: deque[float] = deque()
        self._last_typing_reaction_at = 0.0
        self._focus_active = False
        self._focus_deadline = 0.0
        self._look_direction = "center"
        self._pending_look_direction = "center"
        self._pending_look_since = time.monotonic()
        self._pet_profiles: tuple[PetProfile, ...] = ()
        self._active_pet_id: str | None = None
        self._active_manifest_path: Path | None = None
        self._companion_controller: object | None = None
        self._break_reminder_controller: object | None = None
        self._pixmaps = self._load_pixmaps()
        self._restore_care_state()
        self._selfie_photo = self._load_selfie_photo()
        self._render_cache: OrderedDict[tuple[object, ...], QPixmap] = OrderedDict()
        self._mask_cache: OrderedDict[tuple[object, ...], QRegion] = OrderedDict()

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if settings.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("OnePic Desktop Pet")
        self.setMouseTracking(True)

        source = self._pixmaps[PetState.IDLE][0]
        width = round(settings.display_height * source.width() / source.height())
        self.setFixedSize(width + 12, settings.display_height + 14)
        self.label = QLabel(self)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setGeometry(6, 0, width, settings.display_height + 8)
        self._create_live2d_idle_view()

        self.photo_bubble = QLabel()
        self.photo_bubble.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.photo_bubble.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            True,
        )
        self.photo_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_bubble.setStyleSheet("background: transparent;")

        self.dialogue_bubble = QLabel()
        self.dialogue_bubble.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.dialogue_bubble.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            True,
        )
        self.dialogue_bubble.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.dialogue_bubble.setTextFormat(Qt.TextFormat.PlainText)
        self.dialogue_bubble.setWordWrap(True)
        self.dialogue_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dialogue_bubble.setMaximumWidth(280)
        self.dialogue_bubble.setStyleSheet(
            "QLabel {"
            "background-color: rgba(32, 33, 38, 235);"
            "color: white;"
            "border: 1px solid rgba(255, 255, 255, 90);"
            "border-radius: 12px;"
            "padding: 9px 13px;"
            "font-size: 14px;"
            "}"
        )

        self.movement_timer = QTimer(self)
        self.movement_timer.setInterval(settings.movement_interval_ms)
        self.movement_timer.timeout.connect(self._movement_tick)
        self.movement_timer.start()

        self.state_timer = QTimer(self)
        self.state_timer.setSingleShot(True)
        self.state_timer.timeout.connect(self._state_timeout)

        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self._animation_tick)

        self.extension_timer = QTimer(self)
        self.extension_timer.timeout.connect(self._extension_tick)

        self.turn_timer = QTimer(self)
        self.turn_timer.setSingleShot(True)
        self.turn_timer.timeout.connect(self._finish_turn)

        self.interaction_timer = QTimer(self)
        self.interaction_timer.setSingleShot(True)
        self.interaction_timer.timeout.connect(self._finish_interaction)

        self.touch_timer = QTimer(self)
        self.touch_timer.setSingleShot(True)
        self.touch_timer.timeout.connect(self._advance_touch_sequence)

        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self._trigger_hover_curiosity)

        self.photo_timer = QTimer(self)
        self.photo_timer.setSingleShot(True)
        self.photo_timer.timeout.connect(self.photo_bubble.hide)

        self.dialogue_timer = QTimer(self)
        self.dialogue_timer.setSingleShot(True)
        self.dialogue_timer.timeout.connect(self.dialogue_bubble.hide)

        self.effect_timer = QTimer(self)
        self.effect_timer.setInterval(90)
        self.effect_timer.timeout.connect(self._effect_tick)

        self.bob_timer = QTimer(self)
        self.bob_timer.setInterval(280)
        self.bob_timer.timeout.connect(self._bob_tick)
        self.bob_timer.start()

        self.look_timer = QTimer(self)
        self.look_timer.setInterval(
            int(self._directional_config.get("poll_interval_ms", 70))
        )
        self.look_timer.timeout.connect(self._update_mouse_look)
        if self._directional_pixmaps:
            self.look_timer.start()

        self.keyboard_timer = QTimer(self)
        self.keyboard_timer.timeout.connect(self._poll_keyboard_activity)

        self.focus_timer = QTimer(self)
        self.focus_timer.setSingleShot(True)
        self.focus_timer.timeout.connect(self._complete_focus_session)

        self.autoplay_timer = QTimer(self)
        self.autoplay_timer.setSingleShot(True)
        self.autoplay_timer.timeout.connect(self._autoplay_action_tick)

        self.auto_expression_timer = QTimer(self)
        self.auto_expression_timer.setSingleShot(True)
        self.auto_expression_timer.timeout.connect(
            self._auto_expression_rotation_tick
        )
        self._configure_companion_runtime()

        self.set_state(PetState.IDLE)
        self._schedule(self.behavior.initial_idle())

    def _load_pixmaps(self) -> dict[PetState, list[QPixmap]]:
        """根据素材清单加载各状态帧序列并验证完整性。"""

        manifest_path = resource_path("assets/pet/manifest.json")
        if os.environ.get("ONEPIC_USE_DEMO_ASSETS") == "1":
            self._active_manifest_path = manifest_path
            return self._load_manifest_pixmaps(manifest_path)
        manifest_override = os.environ.get("ONEPIC_PET_MANIFEST")
        if manifest_override:
            manifest_path = Path(manifest_override).expanduser().resolve()
            if not manifest_path.is_file():
                raise FileNotFoundError(
                    f"ONEPIC_PET_MANIFEST 指向的清单不存在：{manifest_path}"
                )
            self._active_manifest_path = manifest_path
            return self._load_manifest_pixmaps(manifest_path)
        catalog = load_pet_profile_catalog()
        if catalog is not None:
            self._pet_profiles = catalog.profiles
            selected = catalog.by_id(self.settings.pet_id or "")
            if selected is None:
                selected = catalog.by_id(catalog.default_pet_id)
            assert selected is not None
            if not character_is_approved(
                load_workflow(workflow_path(pet_id=selected.pet_id))
            ):
                raise WorkflowError(
                    "检测到可切换的私有宠物素材，但标准人物尚未确认。"
                )
            self._active_pet_id = selected.pet_id
            self.settings.pet_id = selected.pet_id
            self._active_manifest_path = selected.manifest_path
            return self._load_manifest_pixmaps(selected.manifest_path)
        try:
            custom_manifest = resource_path("user_assets/pet/manifest.json")
        except FileNotFoundError:
            pass
        else:
            if not character_is_approved(load_workflow()):
                raise WorkflowError(
                    "检测到私有宠物素材，但标准人物尚未确认；拒绝静默回退到演示角色。"
                )
            manifest_path = custom_manifest
        self._active_manifest_path = manifest_path
        return self._load_manifest_pixmaps(manifest_path)

    def _load_manifest_pixmaps(
        self,
        manifest_path: Path,
    ) -> dict[PetState, list[QPixmap]]:
        """从指定清单加载帧；测试可借此固定使用公开演示素材。"""

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._active_display_name = str(
            manifest.get("display_name") or "OnePic Desktop Pet"
        ).strip()
        self.setWindowTitle(self._active_display_name or "OnePic Desktop Pet")
        self._stationary = False
        self._directional_pixmaps = {}
        self._directional_ring_count = 0
        self._directional_config = {}
        self._state_fallbacks = {}
        self._look_direction = "center"
        self._pending_look_direction = "center"
        self._pending_look_since = time.monotonic()
        self._touch_config = {}
        self._companion_config = {}
        self._companion_ui_config = normalize_companion_ui_config(
            manifest.get("companion_ui")
        )
        self._break_reminder_config = manifest.get("break_reminders", {})
        self._care_config = normalize_care_config(
            manifest.get("care_simulation", {})
        )
        self._dialogue_config = normalize_dialogue_config(
            manifest.get("dialogue_system", {})
        )
        self._dialogue_pet_id = str(
            manifest.get("pet_id") or self._active_pet_id or "default"
        )
        self._reset_dialogue_selector()
        self._extension_animations = {}
        self._extension_animation_config = {}
        self._extension_active_name = None
        self._extension_frame_index = 0
        self._extension_finished = None
        self._extension_loop_override = None
        self._extension_single_frame = False
        self._autoplay_recent.clear()
        self._autoplay_active_sequence = None
        self._autoplay_cycles_remaining = 0
        self._walk_variant_pixmaps = {}
        self._walk_variant_labels = {}
        self._walk_variant_mirror = {}
        self._active_walk_variant = "front"
        self._hybrid_runtime_config = {}
        self._touch_transition_pixmaps = []
        self._touch_override_pixmap = None
        raw_state_fallbacks = manifest.get("state_fallbacks", {})
        if not isinstance(raw_state_fallbacks, dict):
            raise ValueError("state_fallbacks 必须是对象")
        for raw_state, raw_fallback in raw_state_fallbacks.items():
            try:
                state = PetState(str(raw_state))
                fallback = PetState(str(raw_fallback))
            except ValueError as exc:
                raise ValueError(
                    f"未知基础状态回退：{raw_state!r} -> {raw_fallback!r}"
                ) from exc
            if state is not fallback:
                self._state_fallbacks[state] = fallback
        self._stationary = bool(manifest.get("stationary", False))
        directional = manifest.get("directional_mouse_follow", {})
        if isinstance(directional, dict) and directional.get("enabled"):
            directional_frames = directional.get("frames", {})
            if not isinstance(directional_frames, dict):
                raise ValueError("directional_mouse_follow.frames 必须是对象")
            loaded_directions: dict[str, QPixmap] = {}
            for name, relative in directional_frames.items():
                path = manifest_path.parent / str(relative)
                pixmap = QPixmap(str(path))
                if not path.is_file() or pixmap.isNull():
                    raise FileNotFoundError(f"缺少鼠标跟随方向素材：{path}")
                loaded_directions[str(name)] = pixmap
            ring_frames = directional.get("ring_frames", [])
            if ring_frames:
                if not isinstance(ring_frames, list):
                    raise ValueError(
                        "directional_mouse_follow.ring_frames 必须是数组"
                    )
                for index, relative in enumerate(ring_frames):
                    path = manifest_path.parent / str(relative)
                    pixmap = QPixmap(str(path))
                    if not path.is_file() or pixmap.isNull():
                        raise FileNotFoundError(
                            f"缺少连续鼠标跟随素材：{path}"
                        )
                    loaded_directions[f"ring_{index:02d}"] = pixmap
                self._directional_ring_count = len(ring_frames)
            if "center" not in loaded_directions:
                raise ValueError("鼠标跟随方向素材必须包含 center")
            self._directional_pixmaps = loaded_directions
            self._directional_config = dict(directional)
        touch = manifest.get("touch_feedback", {})
        if isinstance(touch, dict):
            self._touch_config = dict(touch)
            transition_frames = touch.get("transition_frames", [])
            if not isinstance(transition_frames, list):
                raise ValueError("touch_feedback.transition_frames 必须是数组")
            for relative in transition_frames[:2]:
                path = manifest_path.parent / str(relative)
                pixmap = QPixmap(str(path))
                if not path.is_file() or pixmap.isNull():
                    raise FileNotFoundError(f"缺少摸头过渡帧：{path}")
                self._touch_transition_pixmaps.append(pixmap)
        companion = manifest.get("companion_behaviors", {})
        if not isinstance(companion, dict):
            raise ValueError("companion_behaviors 必须是对象")
        self._companion_config = dict(companion)
        extensions = manifest.get("extension_animations", {})
        if not isinstance(extensions, dict):
            raise ValueError("extension_animations 必须是对象")
        for name, raw_config in extensions.items():
            if not isinstance(raw_config, dict):
                raise ValueError(f"扩展动作 {name} 的配置必须是对象")
            relative_frames = raw_config.get("frames", [])
            if not isinstance(relative_frames, list) or not relative_frames:
                raise ValueError(f"扩展动作 {name} 必须至少包含一帧")
            loaded_frames: list[QPixmap] = []
            for relative in relative_frames:
                path = manifest_path.parent / str(relative)
                pixmap = QPixmap(str(path))
                if not path.is_file() or pixmap.isNull():
                    raise FileNotFoundError(f"缺少扩展动作素材：{path}")
                loaded_frames.append(pixmap)
            self._extension_animations[str(name)] = loaded_frames
            self._extension_animation_config[str(name)] = dict(raw_config)
        hybrid_runtime = manifest.get("hybrid_runtime", {})
        if isinstance(hybrid_runtime, dict) and hybrid_runtime.get("enabled"):
            self._hybrid_runtime_config = dict(hybrid_runtime)
        animations: dict[str, list[str]] = manifest["animations"]
        motion_factors = manifest.get(
            "walk_motion_factors",
            DEFAULT_WALK_MOTION_FACTORS,
        )
        if len(motion_factors) != len(animations["walk"]):
            raise ValueError("走路位移曲线必须与走路动画帧数一致")
        self._walk_motion_factors = tuple(float(value) for value in motion_factors)
        mapping = {
            PetState.IDLE: animations["idle"],
            PetState.WALK: animations["walk"],
            PetState.SIT: animations["sit"],
            PetState.SLEEP: animations["sleep"],
            PetState.WAVE: animations["wave"],
            PetState.HAPPY: animations["happy"],
            PetState.SHY: animations["shy"],
            PetState.SURPRISED: animations["surprised"],
            PetState.ANNOYED: animations["annoyed"],
            PetState.SLEEPY: animations["sleepy"],
            PetState.CURIOUS: animations["curious"],
            PetState.SELFIE: animations["selfie"],
            PetState.DRAG: animations["drag"],
        }
        pixmaps: dict[PetState, list[QPixmap]] = {}
        for state, relative_paths in mapping.items():
            state_frames = []
            for relative in relative_paths:
                path = manifest_path.parent / relative
                if not path.is_file():
                    raise FileNotFoundError(f"缺少宠物素材：{path}")
                pixmap = QPixmap(str(path))
                if pixmap.isNull():
                    raise ValueError(f"无法加载宠物素材：{path}")
                state_frames.append(pixmap)
            if not state_frames:
                raise ValueError(f"状态 {state.value} 没有可用素材帧")
            pixmaps[state] = state_frames
        self._walk_variant_pixmaps = {"front": pixmaps[PetState.WALK]}
        self._walk_variant_labels = {"front": "正面走路"}
        # 正面走路可以为每只宠物指定原始素材所朝的屏幕方向。这样
        # 不对称特征（如半边面具）会始终朝向行进方向，而不是一律
        # 在向左移动时翻转，导致其中一侧看起来倒退。
        front_mirror_direction = str(
            manifest.get("front_walk_mirror_direction", "")
        ).strip().lower()
        if front_mirror_direction not in {"left", "right", "none"}:
            front_mirror_direction = (
                "left"
                if bool(manifest.get("front_walk_mirror_with_direction", True))
                else "none"
            )
        self._front_walk_mirror_direction = front_mirror_direction
        self._walk_variant_mirror = {
            "front": front_mirror_direction != "none"
        }
        default_variant = "front"
        for name, config in self._extension_animation_config.items():
            if not bool(config.get("walk_variant", False)):
                continue
            frames = self._extension_animations.get(name, [])
            if len(frames) != 8:
                raise ValueError(f"走路样式 {name} 必须正好包含 8 帧")
            self._walk_variant_pixmaps[name] = frames
            self._walk_variant_labels[name] = str(
                config.get("walk_variant_label") or name
            )
            self._walk_variant_mirror[name] = bool(
                config.get("mirror_with_direction", True)
            )
            if bool(config.get("walk_variant_default", False)):
                default_variant = name
        requested_variant = self.settings.walk_variant or default_variant
        if requested_variant not in self._walk_variant_pixmaps:
            requested_variant = default_variant
        self._active_walk_variant = requested_variant
        self.settings.walk_variant = requested_variant
        return pixmaps

    def _frames_for_state(self, state: PetState) -> list[QPixmap]:
        """返回当前行为应使用的帧；走路状态尊重用户选择的样式。"""

        if state is PetState.WALK:
            return self._walk_variant_pixmaps.get(
                self._active_walk_variant,
                self._pixmaps[state],
            )
        return self._pixmaps[state]

    def _load_selfie_photo(self) -> QPixmap:
        """只加载用户提供的原始自拍照片，不用生成帧冒充原图。"""

        if self._active_manifest_path is not None:
            try:
                manifest = json.loads(
                    self._active_manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                manifest = {}
            relative = manifest.get("selfie_photo")
            if relative:
                path = (self._active_manifest_path.parent / str(relative)).resolve()
                if path.is_file():
                    photo = QPixmap(str(path))
                    if not photo.isNull():
                        return photo
        for relative in (
            "user_assets/selfie.png",
            "user_assets/selfie.jpg",
            "user_assets/selfie.jpeg",
            "user_assets/image.png",
        ):
            try:
                path = resource_path(relative)
            except FileNotFoundError:
                continue
            photo = QPixmap(str(path))
            if not photo.isNull():
                return photo
        return QPixmap()

    def place_at_start(self) -> None:
        """按已保存位置或主屏幕右下角放置窗口。"""

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        if self.settings.start_x is None or self.settings.start_y is None:
            _minimum_x, maximum_x, _minimum_y, maximum_y = (
                self._visible_position_limits(area)
            )
            x = maximum_x - 24
            y = maximum_y - 12
        else:
            x = self.settings.start_x
            y = self.settings.start_y
        self.move(self._constrained_position(QPoint(x, y)))

    def set_state(self, state: PetState) -> None:
        """切换行为状态、重置帧序号并刷新当前图片。"""

        state = self._state_fallbacks.get(state, state)
        self._clear_extension_animation()
        self.state = state
        self._frame_index = 0
        self._animation_direction = 1
        self._animation_finished = None
        self._turn_paused = False
        self._movement_x = float(self.x())
        self._last_movement_at = time.monotonic()
        self.turn_timer.stop()
        display_state = state
        frame_count = len(self._frames_for_state(display_state))
        if frame_count > 1:
            self.animation_timer.start(self._frame_interval(display_state, 0))
        else:
            self.animation_timer.stop()
        if display_state is PetState.WALK:
            self._apply_frame_offset(display_state)
        else:
            self.label.move(6, 0)
        self._effect_phase = 0
        if emotion_effect_name(display_state) is None:
            self.effect_timer.stop()
        else:
            self.effect_timer.start()
        self._sync_hybrid_view(display_state)
        self._refresh_pixmap()

    def _create_live2d_idle_view(self) -> None:
        """按清单创建 Live2D 待机层，任何依赖或素材错误均安全回退 PNG。"""

        if (
            not self._hybrid_runtime_config
            or self._active_manifest_path is None
            or self._hybrid_runtime_config.get("idle_backend") != "live2d"
        ):
            return
        try:
            from .live2d_view import Live2DIdleView

            view = Live2DIdleView(
                self._active_manifest_path,
                self._hybrid_runtime_config,
                self,
            )
        except (ImportError, OSError, ValueError, FileNotFoundError):
            return
        view.setGeometry(self.rect())
        view.ready_changed.connect(self._live2d_ready_changed)
        self._live2d_view = view

    def _live2d_ready_changed(self, ready: bool) -> None:
        self._live2d_ready = bool(ready)
        if ready and self.state is PetState.IDLE and not self.dragging:
            minimum_ms = self._hybrid_idle_minimum_duration_ms()
            if minimum_ms > 0 and self.state_timer.remainingTime() < minimum_ms:
                # WebGL 模型加载耗时不能侵占自动眨眼的完整播放窗口。
                self.state_timer.start(minimum_ms)
        self._sync_hybrid_view(self.state)

    def _sync_hybrid_view(self, state: PetState) -> None:
        """按显式状态映射显示 Live2D；加载失败时继续使用确定性 PNG。"""

        presentation = self._live2d_presentation(state)
        live2d_with_view = bool(
            self._live2d_view is not None and presentation is not None
        )
        show_live2d = bool(live2d_with_view and self._live2d_ready)
        self.label.setVisible(not show_live2d)
        if self._live2d_view is not None:
            # WebEngine 隐藏时可能暂停合成，造成“等待 ready 才显示、但不显示
            # 就永远不 ready”的死锁。idle 加载期保持透明视图可见，由 PNG
            # 继续兜底；ready 后再盖到最上层并隐藏 PNG。
            self._live2d_view.setVisible(live2d_with_view)
            if show_live2d:
                apply_presentation = getattr(
                    self._live2d_view,
                    "apply_presentation",
                    None,
                )
                if callable(apply_presentation):
                    apply_presentation(
                        presentation,
                        frame_index=(
                            self._extension_frame_index
                            if self._extension_active_name is not None
                            else self._frame_index
                        ),
                    )
                self._live2d_view.raise_()

    def _live2d_presentation(
        self,
        state: PetState,
    ) -> dict[str, object] | None:
        """返回当前基础/扩展状态的模型呈现；旧清单仍只在 idle 使用。"""

        raw_map = self._hybrid_runtime_config.get("live2d_state_map")
        if isinstance(raw_map, dict) and raw_map:
            key = (
                f"extension:{self._extension_active_name}"
                if self._extension_active_name is not None
                else state.value
            )
            presentation = raw_map.get(key)
            return dict(presentation) if isinstance(presentation, dict) else None
        if state is PetState.IDLE and self._extension_active_name is None:
            return {}
        return None

    def _frame_interval(self, state: PetState, frame_index: int) -> int:
        """返回指定动画帧的停留时间，使眨眼、过渡与行走节奏彼此独立。"""

        if state is PetState.IDLE:
            durations = (820, 360, 100, 120, 140, 720)
            return durations[frame_index % len(durations)]
        if state is PetState.WALK:
            if self._active_walk_variant != "front":
                config = self._extension_animation_config.get(
                    self._active_walk_variant,
                    {},
                )
                return min(
                    500,
                    max(
                        50,
                        int(
                            config.get(
                                "frame_duration_ms",
                                self.settings.walk_frame_interval_ms,
                            )
                        ),
                    ),
                )
            return self.settings.walk_frame_interval_ms
        if state is PetState.SIT:
            return 160
        if state is PetState.SLEEP:
            return 180
        if state is PetState.DRAG:
            return 180
        return 380

    @staticmethod
    def _remember_cache_item(cache, key, value) -> None:
        """写入小型最近使用缓存，并限制长期运行时的内存占用。"""

        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > 96:
            cache.popitem(last=False)

    @staticmethod
    def _direction_from_vector(
        dx: float,
        dy: float,
        dead_zone_px: float = 0,
    ) -> str:
        """把屏幕坐标向量稳定映射为中心或八个观察方向。"""

        if math.hypot(dx, dy) <= max(0.0, dead_zone_px):
            return "center"
        angle = math.degrees(math.atan2(-dy, dx)) % 360
        sector = int((angle + 22.5) // 45) % 8
        return (
            "east",
            "north_east",
            "north",
            "north_west",
            "west",
            "south_west",
            "south",
            "south_east",
        )[sector]

    @staticmethod
    def _ring_index_from_vector(dx: float, dy: float, frame_count: int) -> int:
        """把鼠标向量映射到从正上方开始、顺时针排列的连续帧。"""

        if frame_count < 1:
            raise ValueError("连续方向帧数量必须大于零")
        angle = math.degrees(math.atan2(dx, -dy)) % 360
        return round(angle / (360 / frame_count)) % frame_count

    def _update_mouse_look(self) -> None:
        """按全局鼠标位置切换原视频朝向帧，并用短延迟抑制边界闪动。"""

        if not self._directional_pixmaps or self.dragging:
            return
        head_anchor = self.mapToGlobal(
            QPoint(
                round(self.width() * 0.5),
                round(self.height() * 0.31),
            )
        )
        cursor = QCursor.pos()
        dx = cursor.x() - head_anchor.x()
        dy = cursor.y() - head_anchor.y()
        dead_zone = float(
            self._directional_config.get("dead_zone_px", 48)
        )
        if math.hypot(dx, dy) <= dead_zone:
            target = "center"
        elif self._directional_ring_count:
            # Zero degrees is up; positive angles move clockwise so the
            # original video's clockwise head turn can be played both forward
            # and backward as the cursor circles the pet.
            index = self._ring_index_from_vector(
                dx,
                dy,
                self._directional_ring_count,
            )
            target = f"ring_{index:02d}"
        else:
            target = self._direction_from_vector(dx, dy, dead_zone)
        if target not in self._directional_pixmaps:
            target = "center"
        now = time.monotonic()
        if target == self._look_direction:
            self._pending_look_direction = target
            self._pending_look_since = now
            return
        delay_seconds = (
            float(self._directional_config.get("switch_delay_ms", 105)) / 1000
        )
        if delay_seconds <= 0:
            self._pending_look_direction = target
            self._pending_look_since = now
            self._look_direction = target
            self._refresh_pixmap()
            return
        if target != self._pending_look_direction:
            self._pending_look_direction = target
            self._pending_look_since = now
            return
        if now - self._pending_look_since < max(0.0, delay_seconds):
            return
        self._look_direction = target
        self._refresh_pixmap()

    def _current_source(self) -> tuple[PetState, QPixmap]:
        """返回当前显示状态及方向处理后的原始帧。"""

        display_state = self.state
        if self._touch_override_pixmap is not None:
            return display_state, self._touch_override_pixmap
        if self._extension_active_name is not None:
            frames = self._extension_animations[self._extension_active_name]
            index = min(self._extension_frame_index, len(frames) - 1)
            return display_state, frames[index]
        if self._directional_pixmaps and display_state is PetState.IDLE:
            pixmap = self._directional_pixmaps.get(
                self._look_direction,
                self._directional_pixmaps["center"],
            )
            return display_state, pixmap
        frames = self._frames_for_state(display_state)
        pixmap = frames[min(self._frame_index, len(frames) - 1)]
        should_mirror = False
        if display_state is PetState.WALK:
            if self._active_walk_variant == "front":
                should_mirror = (
                    self._front_walk_mirror_direction == "left"
                    and self.direction < 0
                ) or (
                    self._front_walk_mirror_direction == "right"
                    and self.direction >= 0
                )
            else:
                should_mirror = (
                    self.direction < 0
                    and self._walk_variant_mirror.get(
                        self._active_walk_variant,
                        True,
                    )
                )
        if should_mirror:
            pixmap = pixmap.transformed(QTransform().scale(-1, 1))
        return display_state, pixmap

    def _refresh_pixmap(self) -> None:
        """从缓存取得或按当前屏幕设备像素比栅格化当前动画帧。"""

        display_state, pixmap = self._current_source()
        ratio = max(1.0, self.devicePixelRatioF())
        direction_key: object
        if self._extension_active_name is not None:
            direction_key = (
                "extension",
                self._extension_active_name,
                self._extension_frame_index,
            )
        elif self._directional_pixmaps and display_state is PetState.IDLE:
            direction_key = self._look_direction
        else:
            direction_key = (
                (self._active_walk_variant, self.direction)
                if display_state is PetState.WALK
                else 0
            )
        cache_key = (
            display_state,
            self._frame_index,
            direction_key,
            pixmap.cacheKey(),
            (
                self._touch_override_pixmap.cacheKey()
                if self._touch_override_pixmap is not None
                else 0
            ),
            round(ratio, 3),
            self.label.width(),
            self.label.height(),
        )
        scaled = self._render_cache.get(cache_key)
        if scaled is None:
            target = QSize(
                max(1, round(self.label.width() * ratio)),
                max(1, round(self.label.height() * ratio)),
            )
            scaled = pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(ratio)
            self._remember_cache_item(self._render_cache, cache_key, scaled)
        composed = draw_emotion_effect(
            scaled,
            display_state,
            self._effect_phase,
        )
        self.label.setPixmap(composed)
        effect_key = self._effect_phase if emotion_effect_name(display_state) else -1
        self._refresh_window_mask(
            display_state,
            composed,
            direction_key,
            effect_key,
            pixmap.cacheKey(),
        )

    def _refresh_window_mask(
        self,
        display_state: PetState,
        pixmap: QPixmap,
        direction_key: object,
        effect_key: int,
        source_cache_key: int,
    ) -> None:
        """按当前人物轮廓设置窗口遮罩，使透明留白不拦截桌面点击。"""

        cache_key = (
            display_state,
            self._frame_index,
            direction_key,
            effect_key,
            source_cache_key,
            self.label.width(),
            self.label.height(),
        )
        region = self._mask_cache.get(cache_key)
        if region is None:
            logical = pixmap.scaled(
                self.label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            offset_x = (self.label.width() - logical.width()) // 2
            offset_y = (self.label.height() - logical.height()) // 2
            region = QRegion(logical.mask()).translated(offset_x, offset_y)
            self._remember_cache_item(self._mask_cache, cache_key, region)
        window_region = region.translated(self.label.x(), self.label.y())
        live2d_visible = bool(
            self._live2d_view is not None
            and self._live2d_ready
            and self._live2d_presentation(display_state) is not None
        )
        if live2d_visible:
            # Live2D 与精灵帧拥有不同的源画布和透明留白。即使把精灵逐像素
            # 轮廓改成包围框，包围框顶边仍可能落在 Live2D 帽子中段，形成整齐
            # 的水平裁切线。待机时使用完整窗口区域；WebGL 自身保持透明背景，
            # 切回精灵状态后仍恢复逐像素遮罩。
            self.setMask(QRegion(self.rect()))
            return
        self.setMask(window_region)

    def _effect_tick(self) -> None:
        """推进表情符号的轻微漂浮动画并刷新合成帧。"""

        if emotion_effect_name(self.state) is None:
            self.effect_timer.stop()
            return
        self._effect_phase = (self._effect_phase + 1) % 12
        self._refresh_pixmap()

    def _animation_tick(self) -> None:
        """推进循环或单次连续帧，并在反向过渡结束后执行回调。"""

        display_state = self.state
        frames = self._frames_for_state(display_state)
        if len(frames) <= 1:
            return
        if self._animation_direction < 0:
            self._frame_index = max(0, self._frame_index - 1)
            if self._frame_index == 0:
                self.animation_timer.stop()
                callback = self._animation_finished
                self._animation_finished = None
                if callback is not None:
                    QTimer.singleShot(0, callback)
        elif display_state in (PetState.SIT, PetState.SLEEP, PetState.SELFIE):
            self._frame_index = min(self._frame_index + 1, len(frames) - 1)
            if self._frame_index == len(frames) - 1:
                self.animation_timer.stop()
        else:
            self._frame_index = (self._frame_index + 1) % len(frames)
        self._apply_frame_offset(display_state)
        self._refresh_pixmap()
        if self.animation_timer.isActive():
            self.animation_timer.setInterval(
                self._frame_interval(display_state, self._frame_index)
            )

    def _apply_frame_offset(self, display_state: PetState) -> None:
        """按跑步落脚、压缩和腾空阶段同步水平回弹与身体起伏。"""

        if display_state is PetState.WALK:
            x_offsets = (6, 6, 6, 6, 6, 6, 6, 6)
            y_offsets = (2, 3, 2, 1, 2, 3, 2, 1)
            phase = self._frame_index % len(y_offsets)
            self.label.move(x_offsets[phase], y_offsets[phase])

    def _walk_phase_speed_factor(self) -> float:
        """返回当前落脚相位的平滑推进倍率，供自主走路和追鼠标共用。"""

        if not self._walk_motion_factors:
            return 1.0
        return self._walk_motion_factors[
            self._frame_index % len(self._walk_motion_factors)
        ]

    def _movement_speed_pixels_per_second(self) -> float:
        """按旧配置的平均速度计算恒定水平速度。"""

        return (
            self.settings.movement_step
            * 1000.0
            / self.settings.movement_interval_ms
        )

    def showEvent(self, event: QShowEvent) -> None:
        """窗口首次显示时连接跨屏信号并按当前 DPI 绘制。"""

        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._screen_change_connected:
            handle.screenChanged.connect(self._on_screen_changed)
            self._screen_change_connected = True
        self._on_screen_changed(handle.screen() if handle else None)

    def moveEvent(self, event: QMoveEvent) -> None:
        """宠物移动时让可见文字气泡持续跟随。"""

        super().moveEvent(event)
        if (
            hasattr(self, "dialogue_bubble")
            and self.dialogue_bubble.isVisible()
        ):
            self._position_dialogue_bubble()

    def hideEvent(self, event: QHideEvent) -> None:
        """隐藏宠物时同步隐藏两个独立气泡窗口。"""

        if hasattr(self, "dialogue_bubble"):
            self.dialogue_bubble.hide()
        if hasattr(self, "photo_bubble"):
            self.photo_bubble.hide()
        super().hideEvent(event)

    def _on_screen_changed(self, screen: QScreen | None) -> None:
        """切换目标屏幕后重连 DPI 信号并延迟刷新素材。"""

        if self._connected_screen is not None:
            try:
                self._connected_screen.logicalDotsPerInchChanged.disconnect(
                    self._on_dpi_changed
                )
            except (RuntimeError, TypeError):
                pass
        self._connected_screen = screen
        if screen is not None:
            screen.logicalDotsPerInchChanged.connect(self._on_dpi_changed)
        self._render_cache.clear()
        QTimer.singleShot(0, self._refresh_pixmap)

    def _on_dpi_changed(self, _dpi: float) -> None:
        """显示器缩放发生变化时刷新当前帧。"""

        self._render_cache.clear()
        QTimer.singleShot(0, self._refresh_pixmap)

    def _schedule(self, decision: StateDecision) -> None:
        """应用状态决策并安排下一次状态切换。"""

        duration_ms = decision.duration_ms
        if decision.state is PetState.IDLE:
            duration_ms = max(
                duration_ms,
                self._hybrid_idle_minimum_duration_ms(),
            )
        self.set_state(decision.state)
        self._maybe_show_dialogue(
            self._dialogue_action_for_state(decision.state),
            source="autonomous",
        )
        if not self.dragging:
            self.state_timer.start(duration_ms)

    def _hybrid_idle_minimum_duration_ms(self) -> int:
        """确保一次 Live2D 待机足以完整播放最晚开始的自动眨眼。"""

        hybrid = self._hybrid_runtime_config
        if (
            not hybrid
            or not hybrid.get("enabled")
            or hybrid.get("idle_backend") != "live2d"
        ):
            return 0
        animation = hybrid.get("idle_animation", {})
        if not isinstance(animation, dict) or not animation.get("blink", True):
            return 0
        intervals = animation.get("blink_interval_ms", [2600, 4600])
        durations = animation.get("blink_duration_ms", [105, 60, 160])
        if (
            not isinstance(intervals, list)
            or len(intervals) != 2
            or not isinstance(durations, list)
            or len(durations) != 3
        ):
            return 0
        try:
            maximum_interval = int(intervals[1])
            total_duration = sum(int(value) for value in durations)
        except (TypeError, ValueError):
            return 0
        return max(0, maximum_interval + total_duration + 500)

    def _state_timeout(self) -> None:
        """处理自主状态到期，并按无互动时长逐级进入坐下与睡眠。"""

        self._advance_care()
        if self.dragging:
            return
        if self._focus_active:
            if self.state is not PetState.SIT:
                self.set_state(PetState.SIT)
            return
        self.mood.pass_time(self.state)
        inactive_ms = self._inactive_ms()
        if self._sleep_after_sit and self.state is PetState.SIT:
            self._sleep_after_sit = False
            self._schedule(self._decision(PetState.SLEEP, 8000))
            return
        if inactive_ms >= self.settings.inactive_sleep_ms:
            if self.state is PetState.SLEEP:
                self.state_timer.start(10000)
                return
            if self.state is PetState.SIT:
                self._schedule(self._decision(PetState.SLEEP, 10000))
                return
            self._schedule_sleep_via_sit()
            return
        if inactive_ms >= self.settings.inactive_sit_ms:
            if self.state is PetState.SIT:
                remaining = self.settings.inactive_sleep_ms - inactive_ms
                self.state_timer.start(max(500, min(5000, remaining)))
                return
            self._schedule(
                self._decision(
                    PetState.SIT,
                    min(5000, self.settings.inactive_sleep_ms - inactive_ms),
                )
            )
            return
        if self.state in (PetState.SIT, PetState.SLEEP):
            self._reverse_transition_to_idle()
            return
        decision = self._next_autonomous_state(
            self.state,
            allow_walk=not self.paused,
        )
        if decision.state is PetState.SLEEP:
            self._schedule_sleep_via_sit()
        else:
            self._schedule(decision)

    @staticmethod
    def _decision(state: PetState, duration_ms: int) -> StateDecision:
        """创建窗口内部过渡使用的确定时长状态决策。"""

        return StateDecision(state, max(500, duration_ms))

    def _active_care(self) -> PetCareState | None:
        """仅在显式启用时向行为层暴露养成状态。"""

        if self._care_enabled():
            return self.care
        return None

    def _care_enabled(self) -> bool:
        """返回当前宠物清单或开发设置是否启用养成玩法。"""

        return bool(
            self.settings.care_simulation_enabled
            or self._care_config.get("enabled", False)
        )

    def _care_pet_id(self) -> str:
        """返回养成存档使用的稳定宠物 ID。"""

        return str(
            self._active_pet_id
            or self._dialogue_pet_id
            or self.settings.pet_id
            or "default"
        )

    def _restore_care_state(self) -> None:
        """按当前宠物 ID 恢复独立养成存档。"""

        initial_coins = int(self._care_config.get("initial_coins", 60))
        self.care = PetCareState.from_dict(
            self.settings.care_states.get(self._care_pet_id()),
            initial_coins=initial_coins,
        )
        self._last_care_update = time.monotonic()
        self._last_care_action_at = 0.0

    def _store_care_state(self) -> None:
        """把当前养成状态同步到待保存的用户设置。"""

        if not self._care_enabled():
            return
        self.settings.care_states[self._care_pet_id()] = self.care.to_dict()

    def export_care_state(self) -> dict[str, float | int] | None:
        """供应用退出流程推进并导出当前宠物养成存档。"""

        if not self._care_enabled():
            return None
        self._advance_care()
        self._store_care_state()
        return self.care.to_dict()

    def _next_autonomous_state(
        self,
        current: PetState,
        *,
        allow_walk: bool,
    ) -> StateDecision:
        """保持旧两参数扩展点，仅在启用养成时追加需求状态。"""

        care = self._active_care()
        if care is None:
            return self.behavior.next_autonomous_state(
                current,
                allow_walk=allow_walk,
            )
        return self.behavior.next_autonomous_state(
            current,
            allow_walk=allow_walk,
            care=care,
        )

    def _advance_care(self, now: float | None = None) -> None:
        """按真实经过时间推进养成层；关闭时只刷新计时基准。"""

        current = time.monotonic() if now is None else float(now)
        elapsed = max(0.0, current - self._last_care_update)
        self._last_care_update = current
        if self._care_enabled():
            self.care.advance(elapsed, self.state.value)

    def _receive_affection(self) -> None:
        """同步轻量心情层与可选养成层的友好互动。"""

        self.mood.receive_affection()
        if self._care_enabled():
            self.care.comfort()
            self._store_care_state()

    def _begin_care_action(self) -> bool:
        """检查养成功能、拖拽状态和动作冷却。"""

        if not self._care_enabled() or self.dragging:
            return False
        now = time.monotonic()
        cooldown = max(
            200,
            int(self._care_config.get("action_cooldown_ms", 900)),
        ) / 1000
        if now - self._last_care_action_at < cooldown:
            return False
        self._last_care_action_at = now
        self._record_user_interaction()
        self._advance_care(now)
        return True

    def care_feed(self) -> bool:
        """消耗金币喂点心，并复用现有开心动作反馈。"""

        if not self._begin_care_action():
            return False
        cost = int(self._care_config.get("feed_cost", 8))
        if not self.care.try_spend(cost):
            self._show_dialogue_text("金币不够啦，完成一次专注可以获得奖励。")
            return False
        self.care.feed(float(self._care_config.get("feed_amount", 24)))
        self._store_care_state()
        self._show_emotion(PetState.HAPPY, 1700)
        self._show_dialogue_text("点心收到啦，饱腹度恢复了～")
        return True

    def care_drink(self) -> bool:
        """消耗少量金币补充饮水值。"""

        if not self._begin_care_action():
            return False
        cost = int(self._care_config.get("drink_cost", 2))
        if not self.care.try_spend(cost):
            self._show_dialogue_text("暂时买不起饮料，先一起专注一会儿吧。")
            return False
        self.care.drink(float(self._care_config.get("drink_amount", 28)))
        self._store_care_state()
        self._show_emotion(PetState.HAPPY, 1400)
        self._show_dialogue_text("喝到水啦，谢谢你～")
        return True

    def care_play(self) -> bool:
        """消耗活力一起玩，优先复用已批准的跳跃扩展。"""

        if not self._begin_care_action():
            return False
        if not self.care.play(
            vitality_cost=float(
                self._care_config.get("play_vitality_cost", 6)
            ),
            happiness_gain=float(
                self._care_config.get("play_happiness_gain", 14)
            ),
        ):
            self._show_dialogue_text("现在有点累，先休息一下再玩吧。")
            return False
        self._store_care_state()
        jump_sequence = (
            "jump"
            if "jump" in self._extension_animations
            else next(
                (
                    name
                    for name in self._extension_animations
                    if "jump" in name.casefold()
                ),
                "",
            )
        )
        if jump_sequence:
            self.trigger_extension_action(jump_sequence)
        else:
            self._show_emotion(PetState.HAPPY, 1800)
            self._show_dialogue_text("一起玩真开心！")
        return True

    def care_rest(self) -> bool:
        """使用现有坐下到睡眠序列开始休息。"""

        if not self._begin_care_action():
            return False
        self._show_dialogue_text("那我休息一会儿，恢复活力。")
        self._schedule_sleep_via_sit()
        return True

    def _inactive_ms(self) -> int:
        """返回距离最近一次鼠标或菜单互动的毫秒数。"""

        return max(0, round((time.monotonic() - self._last_user_interaction) * 1000))

    def _record_user_interaction(self) -> None:
        """重置无互动计时，并立即让自动微表情给显式操作让路。"""

        self._cancel_auto_expression_for_user_interaction()
        self._last_user_interaction = time.monotonic()
        self._sleep_after_sit = False

    def _cancel_auto_expression_for_user_interaction(self) -> bool:
        """取消正在展示的随机单帧；不完成互动，也不重排自主状态。"""

        if not getattr(self, "_extension_single_frame", False):
            return False
        rotation = self._companion_section("auto_expression_rotation")
        sequence = str(rotation.get("sequence", "")).strip()
        if not sequence or self._extension_active_name != sequence:
            return False
        self._clear_extension_animation()
        self._sync_hybrid_view(self.state)
        self._refresh_pixmap()
        return True

    def _companion_section(self, name: str) -> dict[str, object]:
        """返回一个陪伴行为配置段；无效值安全降级为空配置。"""

        value = self._companion_config.get(name, {})
        return dict(value) if isinstance(value, dict) else {}

    def _dialogue_overrides_for_current_pet(self) -> dict[str, list[str]]:
        """返回当前宠物的用户台词覆盖，不与其他形象混用。"""

        value = self.settings.dialogue_overrides.get(
            self._dialogue_pet_id,
            {},
        )
        return dict(value) if isinstance(value, dict) else {}

    def _reset_dialogue_selector(self) -> None:
        """按当前清单和用户覆盖重建随机选择器。"""

        self._dialogue_selector = DialogueSelector(
            self._dialogue_config,
            self._dialogue_overrides_for_current_pet(),
        )

    def _dialogue_action_for_state(self, state: PetState) -> str:
        """把运行状态转换为台词键，侧面走路使用独立台词池。"""

        if (
            state is PetState.WALK
            and self._active_walk_variant != "front"
        ):
            return self._active_walk_variant
        return state.value

    def _maybe_show_dialogue(
        self,
        action: str,
        *,
        source: str,
        force: bool = False,
        ignore_cooldown: bool = False,
    ) -> bool:
        """按配置选择并显示一句台词；未通过概率或冷却时安全跳过。"""

        text = self._dialogue_selector.choose(
            action,
            source=source,
            force=force,
            ignore_cooldown=ignore_cooldown,
        )
        if text is None:
            return False
        self._show_dialogue_text(text)
        return True

    @staticmethod
    def _configured_base_state(value: object) -> PetState:
        """把扩展动作的承载状态限制在不会移动窗口的安全集合内。"""

        try:
            state = PetState(str(value))
        except ValueError:
            return PetState.IDLE
        if state in {
            PetState.IDLE,
            PetState.SIT,
            PetState.WAVE,
            PetState.HAPPY,
            PetState.SHY,
            PetState.SURPRISED,
            PetState.ANNOYED,
            PetState.SLEEPY,
            PetState.CURIOUS,
        }:
            return state
        return PetState.IDLE

    def _clear_extension_animation(self) -> None:
        """停止当前扩展动作并清理回调，不改变基础行为状态。"""

        if hasattr(self, "extension_timer"):
            self.extension_timer.stop()
        self._extension_active_name = None
        self._extension_frame_index = 0
        self._extension_finished = None
        self._extension_loop_override = None
        self._extension_single_frame = False

    def play_extension_animation(
        self,
        name: str,
        *,
        loop: bool | None = None,
        finished: Callable[[], None] | None = None,
        start_frame: int = 0,
        single_frame: bool = False,
        duration_ms: int | None = None,
    ) -> bool:
        """播放扩展序列或其中一个定时单帧；缺失素材时安全返回 False。"""

        frames = self._extension_animations.get(name)
        config = self._extension_animation_config.get(name)
        if (
            not frames
            or config is None
            or self.dragging
            or self._touch_active
        ):
            return False
        self.touch_timer.stop()
        self._cancel_touch_sequence(restore_idle=False)
        self.state_timer.stop()
        self.interaction_timer.stop()
        base_state = self._configured_base_state(config.get("base_state", "idle"))
        self.set_state(base_state)
        self.animation_timer.stop()
        self._extension_active_name = name
        try:
            requested_frame = int(start_frame)
        except (TypeError, ValueError):
            requested_frame = 0
        self._extension_frame_index = min(
            len(frames) - 1,
            max(0, requested_frame),
        )
        self._extension_finished = finished
        self._extension_loop_override = loop
        self._extension_single_frame = bool(single_frame)
        raw_duration = (
            duration_ms
            if duration_ms is not None
            else config.get("frame_duration_ms", 180)
        )
        try:
            requested_duration = int(raw_duration)
        except (TypeError, ValueError):
            requested_duration = 180
        interval_ms = min(
            5000,
            max(40, requested_duration),
        )
        self.extension_timer.start(interval_ms)
        self._sync_hybrid_view(base_state)
        self._refresh_pixmap()
        return True

    def _extension_tick(self) -> None:
        """推进扩展动作；循环动作持续播放，单次动作结束后回归自主行为。"""

        name = self._extension_active_name
        if name is None:
            self.extension_timer.stop()
            return
        frames = self._extension_animations.get(name, [])
        config = self._extension_animation_config.get(name, {})
        if not frames:
            self._clear_extension_animation()
            self._finish_interaction()
            return
        if self._extension_single_frame:
            callback = self._extension_finished
            self._clear_extension_animation()
            self._sync_hybrid_view(self.state)
            self._refresh_pixmap()
            (callback or self._finish_interaction)()
            return
        next_index = self._extension_frame_index + 1
        if next_index < len(frames):
            self._extension_frame_index = next_index
            self._sync_hybrid_view(self.state)
            self._refresh_pixmap()
            return
        should_loop = (
            bool(self._extension_loop_override)
            if self._extension_loop_override is not None
            else bool(config.get("loop", False))
        )
        if should_loop:
            self._extension_frame_index = 0
            self._sync_hybrid_view(self.state)
            self._refresh_pixmap()
            return
        callback = self._extension_finished
        hold_ms = min(5000, max(0, int(config.get("hold_last_ms", 0))))
        self._clear_extension_animation()
        self._sync_hybrid_view(self.state)
        self._refresh_pixmap()
        QTimer.singleShot(hold_ms, callback or self._finish_interaction)

    def trigger_extension_action(self, name: str) -> bool:
        """从菜单或测试入口触发一组已配置的扩展动作。"""

        if not self.play_extension_animation(name):
            return False
        self._record_user_interaction()
        self._maybe_show_dialogue(name, source="manual", force=True)
        return True

    def attach_companion_controller(self, controller: object | None) -> None:
        """挂接应用层智能陪伴控制器；窗口本身不因此访问网络或写入数据。"""

        self._companion_controller = controller

    def attach_break_reminder_controller(self, controller: object | None) -> None:
        """挂接应用级休息提醒控制器；窗口本身不持有调度时钟。"""

        self._break_reminder_controller = controller

    def break_reminder_config(self) -> object:
        """返回当前角色清单的原始提醒段，严格校验由控制器完成。"""

        return self._break_reminder_config

    def active_manifest_path(self) -> Path | None:
        """返回当前角色运行清单路径，供相对音效路径做根目录约束。"""

        return self._active_manifest_path

    def companion_pet_id(self) -> str:
        """返回智能陪伴数据使用的稳定宠物标识。"""

        return self._active_pet_id or self.settings.pet_id or "demo"

    def companion_bond(self) -> float:
        """返回 0–100 的当前好感值，只供生成语气上下文。"""

        if self._care_enabled():
            return min(100.0, max(0.0, float(self.care.bond)))
        return float(min(100, max(0, self.mood.affinity)))

    def show_companion_text(self, text: object) -> None:
        """在现有安全气泡中显示陪伴层纯文本，不解释 HTML 或执行指令。"""

        value = str(text).strip()
        if value:
            # 陪伴工具是用户显式触发的功能，不应受可选随机台词包
            # enabled 开关影响；但仍让自拍图片气泡保持视觉优先级。
            self._show_dialogue_text(value[:600], force=True)

    def trigger_companion_action(self, name: object) -> bool:
        """仅触发基础情绪或当前已装配的扩展动作。"""

        action = str(name).strip().lower()
        if action in self._extension_animations:
            return self.trigger_extension_action(action)
        allowed_states = {
            state.value: state
            for state in (
                PetState.WAVE,
                PetState.HAPPY,
                PetState.SHY,
                PetState.SURPRISED,
                PetState.ANNOYED,
                PetState.SLEEPY,
                PetState.CURIOUS,
            )
        }
        state = allowed_states.get(action)
        if state is None or self.dragging:
            return False
        self._record_user_interaction()
        self._show_emotion(
            state,
            1800,
            dialogue_action=state.value,
            dialogue_source="companion",
        )
        return True

    def present_system_reminder(
        self,
        action_name: object,
        text: object,
        *,
        duration_ms: int = 1800,
    ) -> bool:
        """展示系统提醒动作与纯文本，不重置用户无操作或养成计时。"""

        action = str(action_name).strip().lower()
        allowed_states = {
            state.value: state
            for state in (
                PetState.WAVE,
                PetState.HAPPY,
                PetState.SHY,
                PetState.SURPRISED,
                PetState.ANNOYED,
                PetState.SLEEPY,
                PetState.CURIOUS,
            )
        }
        state = allowed_states.get(action)
        shown = False
        if state is not None and not self.dragging:
            self._show_emotion(
                state,
                min(5000, max(500, int(duration_ms))),
            )
            shown = True
        value = str(text).strip()
        if value:
            self._show_dialogue_text(value[:600], force=True)
            shown = True
        return shown

    @staticmethod
    def _configured_emotion(
        value: object,
        default: PetState,
    ) -> PetState:
        """把配置值限制为可安全短播的表情或互动状态。"""

        try:
            state = PetState(str(value))
        except ValueError:
            return default
        if state in {
            PetState.WAVE,
            PetState.HAPPY,
            PetState.SHY,
            PetState.SURPRISED,
            PetState.ANNOYED,
            PetState.SLEEPY,
            PetState.CURIOUS,
            PetState.SELFIE,
        }:
            return state
        return default

    def _configure_companion_runtime(self) -> None:
        """按当前角色清单启停陪伴计时器，并应用追鼠标默认值。"""

        cursor = self._companion_section("cursor_chase")
        self._cursor_chase_enabled = bool(
            cursor.get("enabled", False)
            and cursor.get("default_enabled", False)
        )
        self._cursor_hugging = False

        keyboard = self._companion_section("keyboard_companion")
        interval_ms = min(
            500,
            max(40, int(keyboard.get("poll_interval_ms", 80))),
        )
        self.keyboard_timer.setInterval(interval_ms)
        self._typing_press_times.clear()
        self._keyboard_monitor.reset()
        if keyboard.get("enabled", False):
            self.keyboard_timer.start()
        else:
            self.keyboard_timer.stop()

        self.autoplay_timer.stop()
        self._autoplay_recent.clear()
        self._autoplay_active_sequence = None
        self._autoplay_cycles_remaining = 0
        self._schedule_autoplay_action(initial=True)

        self.auto_expression_timer.stop()
        self._auto_expression_recent.clear()
        self._schedule_auto_expression_rotation(initial=True)

    def _autoplay_entries(self) -> list[tuple[str, float, int]]:
        """返回当前清单中确实已装配的自动动作及权重、循环次数。"""

        config = self._companion_section("auto_signature_actions")
        raw_actions = config.get("actions", [])
        if not config.get("enabled", False) or not isinstance(raw_actions, list):
            return []
        entries: list[tuple[str, float, int]] = []
        for raw_action in raw_actions:
            if not isinstance(raw_action, dict):
                continue
            sequence = str(raw_action.get("sequence", "")).strip()
            if sequence not in self._extension_animations:
                continue
            try:
                weight = max(0.0, float(raw_action.get("weight", 1.0)))
                cycles = min(4, max(1, int(raw_action.get("cycles", 1))))
            except (TypeError, ValueError):
                continue
            if weight > 0:
                entries.append((sequence, weight, cycles))
        return entries

    def _schedule_autoplay_action(
        self,
        *,
        initial: bool = False,
        retry: bool = False,
    ) -> None:
        """按配置的随机冷却安排下一次动作，避免机械的固定节拍。"""

        if not self._autoplay_entries():
            self.autoplay_timer.stop()
            return
        config = self._companion_section("auto_signature_actions")
        key = (
            "initial_delay_ms"
            if initial
            else "retry_delay_ms"
            if retry
            else "interval_ms"
        )
        defaults = {
            "initial_delay_ms": (5000, 9000),
            "retry_delay_ms": (2500, 4500),
            "interval_ms": (18000, 32000),
        }
        raw_range = config.get(key, list(defaults[key]))
        try:
            if not isinstance(raw_range, list) or len(raw_range) != 2:
                raise ValueError
            minimum = max(250, int(raw_range[0]))
            maximum = max(minimum, int(raw_range[1]))
        except (TypeError, ValueError):
            minimum, maximum = defaults[key]
        self.autoplay_timer.start(random.randint(minimum, maximum))

    def _autoplay_action_blocked(self) -> bool:
        """判断自动动作是否会打断追逐、输入、触摸或其他显式互动。"""

        config = self._companion_section("auto_signature_actions")
        minimum_idle_ms = max(0, int(config.get("minimum_idle_ms", 3500)))
        if (
            self.dragging
            or self._press_pending
            or self._touch_active
            or self._focus_active
            or self._turn_paused
            or self._extension_active_name is not None
            or self.interaction_timer.isActive()
            or self._inactive_ms() < minimum_idle_ms
        ):
            return True
        if self.state in {PetState.IDLE, PetState.SIT}:
            return False
        return not (
            self.state is PetState.DRAG
            and self._cursor_chase_enabled
            and self._cursor_hugging
        )

    def _autoplay_action_tick(self) -> bool:
        """在真正空闲时选取一项动作，并保证最近若干次不重复。"""

        entries = self._autoplay_entries()
        if not entries:
            return False
        if self._autoplay_action_blocked():
            self._schedule_autoplay_action(retry=True)
            return False
        config = self._companion_section("auto_signature_actions")
        chance = min(1.0, max(0.0, float(config.get("chance", 0.78))))
        if random.random() > chance:
            self._schedule_autoplay_action()
            return False
        no_repeat_count = min(
            max(0, len(entries) - 1),
            max(0, int(config.get("no_repeat_count", 2))),
        )
        excluded = (
            set(list(self._autoplay_recent)[-no_repeat_count:])
            if no_repeat_count
            else set()
        )
        eligible = [entry for entry in entries if entry[0] not in excluded]
        if not eligible:
            eligible = entries
        sequence, _weight, cycles = random.choices(
            eligible,
            weights=[entry[1] for entry in eligible],
            k=1,
        )[0]
        self._cursor_hugging = False
        self._autoplay_active_sequence = sequence
        self._autoplay_cycles_remaining = cycles
        if not self.play_extension_animation(
            sequence,
            finished=self._finish_autoplay_cycle,
        ):
            self._autoplay_active_sequence = None
            self._autoplay_cycles_remaining = 0
            self._schedule_autoplay_action(retry=True)
            return False
        if no_repeat_count:
            self._autoplay_recent.append(sequence)
            while len(self._autoplay_recent) > no_repeat_count:
                self._autoplay_recent.popleft()
        else:
            self._autoplay_recent.clear()
        return True

    def _finish_autoplay_cycle(self) -> None:
        """按动作配置完成有限轮次，然后回到自主行为并重新计时。"""

        sequence = self._autoplay_active_sequence
        if (
            sequence
            and self._autoplay_cycles_remaining > 1
            and not self.dragging
            and not self._touch_active
            and not self._focus_active
        ):
            self._autoplay_cycles_remaining -= 1
            if self.play_extension_animation(
                sequence,
                finished=self._finish_autoplay_cycle,
            ):
                return
        self._autoplay_active_sequence = None
        self._autoplay_cycles_remaining = 0
        self._finish_interaction()
        self._schedule_autoplay_action()

    def _auto_expression_rotation_spec(self) -> tuple[str, int] | None:
        """返回可自动抽取的扩展序列及帧数；无效配置安全禁用。"""

        config = self._companion_section("auto_expression_rotation")
        sequence = str(config.get("sequence", "")).strip()
        frames = self._extension_animations.get(sequence, [])
        if not config.get("enabled", False) or not frames:
            return None
        return sequence, len(frames)

    def _schedule_auto_expression_rotation(
        self,
        *,
        initial: bool = False,
        retry: bool = False,
    ) -> None:
        """安排下一次微表情抽取，并给交互冲突使用较短重试间隔。"""

        if self._auto_expression_rotation_spec() is None:
            self.auto_expression_timer.stop()
            return
        config = self._companion_section("auto_expression_rotation")
        key = (
            "initial_delay_ms"
            if initial
            else "retry_delay_ms"
            if retry
            else "interval_ms"
        )
        defaults = {
            "initial_delay_ms": (5000, 8000),
            "retry_delay_ms": (1800, 3200),
            "interval_ms": (12000, 20000),
        }
        raw_range = config.get(key, list(defaults[key]))
        try:
            if not isinstance(raw_range, list) or len(raw_range) != 2:
                raise ValueError
            minimum = max(250, int(raw_range[0]))
            maximum = max(minimum, int(raw_range[1]))
        except (TypeError, ValueError):
            minimum, maximum = defaults[key]
        self.auto_expression_timer.start(random.randint(minimum, maximum))

    def _auto_expression_rotation_blocked(self) -> bool:
        """避免随机表情打断移动、显式互动、专注或另一组扩展动作。"""

        config = self._companion_section("auto_expression_rotation")
        try:
            minimum_idle_ms = max(
                0,
                int(config.get("minimum_idle_ms", 2600)),
            )
        except (TypeError, ValueError):
            minimum_idle_ms = 2600
        return bool(
            self.dragging
            or self._press_pending
            or self._touch_active
            or self._focus_active
            or self._turn_paused
            or self._extension_active_name is not None
            or self.interaction_timer.isActive()
            or self._inactive_ms() < minimum_idle_ms
            or self.state is not PetState.IDLE
        )

    def _auto_expression_rotation_tick(self) -> bool:
        """空闲时随机短暂显示一个微表情，不计作点击或用户互动。"""

        spec = self._auto_expression_rotation_spec()
        if spec is None:
            return False
        if self._auto_expression_rotation_blocked():
            self._schedule_auto_expression_rotation(retry=True)
            return False
        config = self._companion_section("auto_expression_rotation")
        try:
            chance = min(1.0, max(0.0, float(config.get("chance", 1.0))))
        except (TypeError, ValueError):
            chance = 1.0
        if random.random() > chance:
            self._schedule_auto_expression_rotation()
            return False

        sequence, frame_count = spec
        try:
            requested_no_repeat = int(config.get("no_repeat_count", 2))
        except (TypeError, ValueError):
            requested_no_repeat = 2
        no_repeat_count = min(
            max(0, frame_count - 1),
            max(0, requested_no_repeat),
        )
        excluded = (
            set(list(self._auto_expression_recent)[-no_repeat_count:])
            if no_repeat_count
            else set()
        )
        eligible = [index for index in range(frame_count) if index not in excluded]
        if not eligible:
            eligible = list(range(frame_count))
        frame_index = random.choice(eligible)

        raw_duration = config.get("duration_ms", [1200, 1800])
        try:
            if not isinstance(raw_duration, list) or len(raw_duration) != 2:
                raise ValueError
            minimum_duration = max(120, int(raw_duration[0]))
            maximum_duration = max(minimum_duration, int(raw_duration[1]))
        except (TypeError, ValueError):
            minimum_duration, maximum_duration = 1200, 1800
        duration_ms = random.randint(minimum_duration, maximum_duration)

        if not self.play_extension_animation(
            sequence,
            start_frame=frame_index,
            single_frame=True,
            duration_ms=duration_ms,
        ):
            self._schedule_auto_expression_rotation(retry=True)
            return False
        if no_repeat_count:
            self._auto_expression_recent.append(frame_index)
            while len(self._auto_expression_recent) > no_repeat_count:
                self._auto_expression_recent.popleft()
        else:
            self._auto_expression_recent.clear()
        self._schedule_auto_expression_rotation()
        return True

    def set_cursor_chase(self, enabled: bool) -> None:
        """显式启停追逐鼠标演示，关闭后恢复普通自主生活。"""

        cursor = self._companion_section("cursor_chase")
        self._cursor_chase_enabled = bool(
            cursor.get("enabled", False) and enabled
        )
        self._cursor_hugging = False
        self._record_user_interaction()
        if self._cursor_chase_enabled and not self._focus_active:
            self.state_timer.stop()
            self.interaction_timer.stop()
            self.set_state(PetState.WALK)
        elif not self.dragging and not self._focus_active:
            self._schedule(self.behavior.initial_idle())

    def _cursor_chase_tick(
        self,
        now: float,
        elapsed: float,
        cursor: QPoint | None = None,
    ) -> bool:
        """向鼠标二维移动并在接近时切换为抱住姿态。"""

        if (
            not self._cursor_chase_enabled
            or self._stationary
            or self.paused
            or self.dragging
            or self._touch_active
            or self._focus_active
            or self._extension_active_name is not None
        ):
            return False
        config = self._companion_section("cursor_chase")
        target = cursor or QCursor.pos()
        center = self.frameGeometry().center()
        dx = target.x() - center.x()
        dy = target.y() - center.y()
        distance = math.hypot(dx, dy)
        hug_distance = max(20.0, float(config.get("hug_distance_px", 56)))
        release_distance = max(
            hug_distance + 10.0,
            float(config.get("release_distance_px", 105)),
        )
        if distance <= hug_distance or (
            self._cursor_hugging and distance <= release_distance
        ):
            if not self._cursor_hugging:
                self._cursor_hugging = True
                self.state_timer.stop()
                self.interaction_timer.stop()
                self.set_state(PetState.DRAG)
                self._maybe_show_dialogue(
                    "cursor_hug",
                    source="companion",
                )
            return True
        if self._cursor_hugging:
            self._cursor_hugging = False
        if self.state is not PetState.WALK:
            self.state_timer.stop()
            self.interaction_timer.stop()
            self.set_state(PetState.WALK)
        if distance <= 0.5:
            return True
        facing_dead_zone = min(
            120.0,
            max(2.0, float(config.get("facing_dead_zone_px", 22))),
        )
        new_direction = self.direction
        if dx > facing_dead_zone:
            new_direction = 1
        elif dx < -facing_dead_zone:
            new_direction = -1
        if new_direction != self.direction:
            self.direction = new_direction
            self._frame_index = 0
            self._refresh_pixmap()
        speed = min(
            600.0,
            max(40.0, float(config.get("speed_pixels_per_second", 180))),
        )
        slowdown_distance = max(
            hug_distance + 20.0,
            float(config.get("slowdown_distance_px", 180)),
        )
        if distance < slowdown_distance:
            ratio = (distance - hug_distance) / (
                slowdown_distance - hug_distance
            )
            ratio = min(1.0, max(0.0, ratio))
            smooth_ratio = ratio * ratio * (3.0 - 2.0 * ratio)
            minimum_speed_ratio = min(
                0.8,
                max(0.1, float(config.get("minimum_speed_ratio", 0.28))),
            )
            speed *= minimum_speed_ratio + (
                1.0 - minimum_speed_ratio
            ) * smooth_ratio
        speed *= self._walk_phase_speed_factor()
        step = min(distance, speed * max(0.0, elapsed))
        next_position = QPoint(
            round(self.x() + dx / distance * step),
            round(self.y() + dy / distance * step),
        )
        constrained = self._movement_constrained_position(next_position)
        self.move(constrained)
        self._movement_x = float(constrained.x())
        self._last_user_interaction = now
        return True

    def _poll_keyboard_activity(self) -> None:
        """轮询按键活跃数量；不读取或保存任何具体按键。"""

        self._handle_keyboard_activity(
            self._keyboard_monitor.poll_pressed_count()
        )

    def _handle_keyboard_activity(
        self,
        pressed_count: int,
        *,
        now: float | None = None,
    ) -> bool:
        """把短时间连续按键转换为一次克制的陪打字反应。"""

        config = self._companion_section("keyboard_companion")
        if (
            not config.get("enabled", False)
            or pressed_count <= 0
            or self.dragging
            or self._touch_active
            or self._focus_active
            or self._cursor_hugging
        ):
            return False
        current = time.monotonic() if now is None else float(now)
        window_seconds = max(
            0.3,
            float(config.get("burst_window_ms", 1800)) / 1000,
        )
        for _ in range(int(pressed_count)):
            self._typing_press_times.append(current)
        while (
            self._typing_press_times
            and current - self._typing_press_times[0] > window_seconds
        ):
            self._typing_press_times.popleft()
        threshold = max(2, int(config.get("burst_key_count", 5)))
        cooldown_seconds = max(
            1.0,
            float(config.get("reaction_cooldown_ms", 5000)) / 1000,
        )
        if (
            len(self._typing_press_times) < threshold
            or current - self._last_typing_reaction_at < cooldown_seconds
        ):
            return False
        self._typing_press_times.clear()
        self._last_typing_reaction_at = current
        self._record_user_interaction()
        sequence = str(config.get("reaction_sequence", "")).strip()
        if sequence and self.play_extension_animation(sequence):
            self._maybe_show_dialogue("typing", source="companion")
            return True
        state = self._configured_emotion(
            config.get("reaction_state", "wave"),
            PetState.WAVE,
        )
        self._show_emotion(
            state,
            1200,
            dialogue_action="typing",
            dialogue_source="companion",
        )
        return True

    def start_focus_session(self, duration_ms: int | None = None) -> bool:
        """开始一次安静陪伴的专注计时，默认使用角色配置的 25 分钟。"""

        config = self._companion_section("focus_session")
        if not config.get("enabled", False):
            return False
        if duration_ms is None:
            duration_ms = round(
                max(1.0, float(config.get("duration_minutes", 25))) * 60000
            )
        duration_ms = max(100, int(duration_ms))
        self._focus_active = True
        self._focus_deadline = time.monotonic() + duration_ms / 1000
        self._cursor_hugging = False
        self.state_timer.stop()
        self.interaction_timer.stop()
        self.touch_timer.stop()
        self._cancel_touch_sequence(restore_idle=False)
        sequence = str(config.get("loop_sequence", "")).strip()
        if not sequence or not self.play_extension_animation(sequence, loop=True):
            self.set_state(PetState.SIT)
        self.focus_timer.start(duration_ms)
        self._maybe_show_dialogue(
            "focus_start",
            source="manual",
            force=True,
        )
        return True

    def focus_remaining_ms(self) -> int:
        """返回当前专注计时剩余毫秒数。"""

        if not self._focus_active:
            return 0
        return max(0, round((self._focus_deadline - time.monotonic()) * 1000))

    def cancel_focus_session(self) -> None:
        """取消专注计时并恢复普通自主行为。"""

        if not self._focus_active:
            return
        self._focus_active = False
        self._focus_deadline = 0.0
        self.focus_timer.stop()
        self._clear_extension_animation()
        if not self.dragging:
            self._schedule(self.behavior.initial_idle())

    def _complete_focus_session(self) -> None:
        """完成专注计时并播放配置的庆祝表情。"""

        if not self._focus_active:
            return
        self._focus_active = False
        self._focus_deadline = 0.0
        self._clear_extension_animation()
        if self._care_enabled():
            reward_coins = int(
                self._care_config.get("focus_reward_coins", 18)
            )
            reward_experience = float(
                self._care_config.get("focus_reward_experience", 10)
            )
            self.care.reward(
                coins=reward_coins,
                experience=reward_experience,
            )
            self._store_care_state()
        config = self._companion_section("focus_session")
        state = self._configured_emotion(
            config.get("celebration_state", "happy"),
            PetState.HAPPY,
        )
        self._show_emotion(
            state,
            2800,
            dialogue_action="focus_complete",
            dialogue_source="companion",
            force_dialogue=True,
        )

    def trigger_signature_action(self) -> bool:
        """播放角色清单声明的专属招牌动作。"""

        config = self._companion_section("signature_action")
        if not config.get("enabled", False) or self.dragging:
            return False
        sequence = str(config.get("sequence", "")).strip()
        if sequence and self.play_extension_animation(sequence):
            self._record_user_interaction()
            self._maybe_show_dialogue(
                sequence,
                source="manual",
                force=True,
            )
            return True
        state = self._configured_emotion(
            config.get("state", "wave"),
            PetState.WAVE,
        )
        duration_ms = max(500, int(config.get("duration_ms", 2200)))
        self._record_user_interaction()
        self._show_emotion(
            state,
            duration_ms,
            dialogue_action=state.value,
            dialogue_source="manual",
            force_dialogue=True,
        )
        return True

    def _schedule_sleep_via_sit(self) -> None:
        """先完整坐下，再从坐姿播放入睡序列。"""

        self._sleep_after_sit = True
        self._schedule(self._decision(PetState.SIT, 1400))

    def _reverse_transition_to_idle(self) -> None:
        """倒放坐下或睡眠序列，完成自然起身后再进入待机。"""

        frames = self._pixmaps[self.state]
        self._frame_index = len(frames) - 1
        self._animation_direction = -1
        self._animation_finished = self._finish_reverse_transition
        self._refresh_pixmap()
        self.animation_timer.start(self._frame_interval(self.state, self._frame_index))

    def _finish_reverse_transition(self) -> None:
        """睡醒后先回到坐姿，再倒放坐下序列恢复站立待机。"""

        if self.dragging:
            return
        if self.state is PetState.SLEEP:
            self.state = PetState.SIT
            self._frame_index = len(self._pixmaps[PetState.SIT]) - 1
            self._animation_direction = -1
            self._animation_finished = self._finish_reverse_transition
            self._refresh_pixmap()
            self.animation_timer.start(
                self._frame_interval(PetState.SIT, self._frame_index)
            )
            return
        self._schedule(self.behavior.initial_idle())

    def _screen_geometry(self):
        """返回窗口中心所在屏幕的可用区域。"""

        center = self.frameGeometry().center()
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else None

    def _configured_movement_region(self) -> QRect | None:
        """把用户保存的全局坐标活动区域转换为 QRect。"""

        raw = self.settings.movement_region
        if raw is None or len(raw) != 4:
            return None
        x, y, width, height = (int(value) for value in raw)
        if width < 120 or height < 120:
            return None
        return QRect(x, y, width, height)

    def _movement_geometry(self) -> QRect | None:
        """返回自主走动和追鼠标实际使用的可见活动区域。"""

        if not self.settings.movement_region_enabled:
            return self._screen_geometry()
        configured = self._configured_movement_region()
        if configured is None:
            return self._screen_geometry()
        intersections = [
            configured.intersected(screen.availableGeometry())
            for screen in QApplication.screens()
        ]
        visible = [area for area in intersections if not area.isEmpty()]
        if not visible:
            return self._screen_geometry()
        return max(visible, key=lambda area: area.width() * area.height())

    def _constrained_to_area(self, position: QPoint, area: QRect) -> QPoint:
        """按角色可见轮廓把窗口坐标夹在指定矩形内。"""

        minimum_x, maximum_x, minimum_y, maximum_y = (
            self._visible_position_limits(area)
        )
        return QPoint(
            min(max(position.x(), minimum_x), maximum_x),
            min(max(position.y(), minimum_y), maximum_y),
        )

    def _constrained_position(self, position: QPoint) -> QPoint:
        """把拖动、缩放等手动目标限制在屏幕内，不应用活动区域。"""

        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        if screen is None:
            return position
        return self._constrained_to_area(position, screen.availableGeometry())

    def _movement_constrained_position(self, position: QPoint) -> QPoint:
        """只为自主走动和追鼠标应用可选活动区域。"""

        if not self.settings.movement_region_enabled:
            return self._constrained_position(position)
        area = self._movement_geometry()
        if area is None:
            return position
        return self._constrained_to_area(position, area)

    def _visible_position_limits(
        self,
        area: QRect,
    ) -> tuple[int, int, int, int]:
        """返回让透明遮罩可见包围框恰好贴边时的窗口坐标范围。"""

        visible = self.mask().boundingRect()
        if visible.isEmpty():
            visible = self.rect()
        minimum_x = area.left() - visible.left()
        maximum_x = area.right() - visible.right()
        minimum_y = area.top() - visible.top()
        maximum_y = area.bottom() - visible.bottom()
        if minimum_x > maximum_x:
            centered_x = area.center().x() - visible.center().x()
            minimum_x = maximum_x = centered_x
        if minimum_y > maximum_y:
            centered_y = area.center().y() - visible.center().y()
            minimum_y = maximum_y = centered_y
        return minimum_x, maximum_x, minimum_y, maximum_y

    def _movement_tick(self) -> None:
        """按实际经过时间亚像素累计移动，并在屏幕边缘转向。"""

        now = time.monotonic()
        elapsed = min(0.1, max(0.0, now - self._last_movement_at))
        self._last_movement_at = now
        if self._cursor_chase_tick(now, elapsed):
            return
        if self._stationary:
            self._movement_x = float(self.x())
            return
        if (
            self.paused
            or self.dragging
            or self._turn_paused
            or self.state is not PetState.WALK
        ):
            self._movement_x = float(self.x())
            return
        area = self._movement_geometry()
        if area is None:
            return
        if abs(self._movement_x - self.x()) > 1.5:
            self._movement_x = float(self.x())
        minimum, maximum, _minimum_y, _maximum_y = (
            self._visible_position_limits(area)
        )
        direction = 1 if self.direction >= 0 else -1
        phase_factor = self._walk_phase_speed_factor()
        self._movement_x += direction * (
            self._movement_speed_pixels_per_second()
            * phase_factor
            * elapsed
        )
        if self._movement_x <= minimum:
            self._movement_x = float(minimum)
            direction = 1
        elif self._movement_x >= maximum:
            self._movement_x = float(maximum)
            direction = -1
        if direction != self.direction:
            self.direction = direction
            self._frame_index = 0
            self._turn_paused = True
            self.animation_timer.stop()
            self._refresh_pixmap()
            self.turn_timer.start(self.settings.turn_pause_ms)
        self.move(
            self._constrained_to_area(
                QPoint(round(self._movement_x), self.y()),
                area,
            )
        )

    def _finish_turn(self) -> None:
        """结束屏幕边缘的短暂停顿，并从第一帧恢复行走。"""

        self._turn_paused = False
        self._movement_x = float(self.x())
        self._last_movement_at = time.monotonic()
        if self.state is PetState.WALK and not self.paused and not self.dragging:
            self.animation_timer.start(
                self._frame_interval(PetState.WALK, self._frame_index)
            )

    def _bob_tick(self) -> None:
        """通过标签轻微上下移动营造呼吸和行走起伏。"""

        if self._extension_active_name is not None:
            if self.label.pos() != QPoint(6, 0):
                self.label.move(6, 0)
                self._refresh_pixmap()
            return
        if self._stationary:
            if self.label.pos() != QPoint(6, 0):
                self.label.move(6, 0)
                self._refresh_pixmap()
            return
        if self.state is PetState.WALK:
            return
        if self.state not in (
            PetState.IDLE,
            PetState.HAPPY,
            PetState.SHY,
            PetState.SURPRISED,
            PetState.ANNOYED,
            PetState.SLEEPY,
            PetState.CURIOUS,
        ):
            self.label.move(6, 0)
            self._refresh_pixmap()
            return
        self._bob_phase = not self._bob_phase
        self.label.move(6, 2 if self._bob_phase else 0)
        self._refresh_pixmap()

    def set_paused(self, paused: bool) -> None:
        """暂停或恢复跑动；暂停期间仍继续坐下、睡眠和自拍等生活状态。"""

        self._record_user_interaction()
        self.paused = paused
        self.pause_changed.emit(paused)
        if paused and self.state is PetState.WALK:
            self.state_timer.stop()
            decision = self._next_autonomous_state(
                PetState.IDLE,
                allow_walk=False,
            )
            if decision.state is PetState.SLEEP:
                self._schedule_sleep_via_sit()
            else:
                self._schedule(decision)
        elif not paused and not self.dragging and not self.state_timer.isActive():
            self._schedule(self.behavior.initial_idle())

    def _suggested_movement_region(self) -> QRect:
        """返回围绕当前位置、且完整落在当前屏幕内的 800×500 默认区域。"""

        area = self._screen_geometry()
        if area is None:
            return QRect(0, 0, 800, 500)
        width = min(800, area.width())
        height = min(500, area.height())
        center = self.frameGeometry().center()
        x = min(
            max(center.x() - width // 2, area.left()),
            area.right() - width + 1,
        )
        y = min(
            max(center.y() - height // 2, area.top()),
            area.bottom() - height + 1,
        )
        return QRect(x, y, width, height)

    def set_movement_region_enabled(self, enabled: bool) -> None:
        """切换自主走动/追鼠标区域限制，不移动正在被拖放的宠物。"""

        self._record_user_interaction()
        if enabled and self._configured_movement_region() is None:
            suggested = self._suggested_movement_region()
            self.settings.movement_region = suggested.getRect()
        self.settings.movement_region_enabled = bool(enabled)
        self._movement_x = float(self.x())

    def set_movement_region(self, region: QRect) -> None:
        """保存经过尺寸与可见屏幕校验的全局坐标活动区域。"""

        if region.width() < 120 or region.height() < 120:
            raise ValueError("活动区域的宽和高都不能小于 120 像素")
        if region.width() > 20000 or region.height() > 20000:
            raise ValueError("活动区域的宽和高都不能超过 20000 像素")
        if not any(
            not region.intersected(screen.availableGeometry()).isEmpty()
            for screen in QApplication.screens()
        ):
            raise ValueError("活动区域必须与至少一块屏幕相交")
        self._record_user_interaction()
        self.settings.movement_region = region.getRect()
        self.settings.movement_region_enabled = True
        self._movement_x = float(self.x())

    def choose_movement_region(self) -> None:
        """让用户以 X、Y、宽、高设置活动区域，并在成功后自动启用。"""

        region = self._configured_movement_region()
        if region is None:
            region = self._suggested_movement_region()
        default_text = ", ".join(str(value) for value in region.getRect())
        text, accepted = QInputDialog.getText(
            self,
            "设置活动区域",
            "输入全局坐标：X, Y, 宽, 高\n仅限制自主走动和追鼠标；手动拖动不受此区域限制。",
            QLineEdit.EchoMode.Normal,
            default_text,
        )
        if not accepted:
            return
        try:
            values = [
                int(part.strip())
                for part in text.replace("，", ",").split(",")
                if part.strip()
            ]
            if len(values) != 4:
                raise ValueError("请输入四个整数")
            self.set_movement_region(QRect(*values))
        except ValueError as exc:
            QMessageBox.warning(self, "活动区域无效", str(exc))

    def set_walk_variant(self, name: str) -> None:
        """切换八相位走路素材并保存偏好，不改动已批准的其他动作。"""

        if name not in self._walk_variant_pixmaps:
            raise ValueError(f"未知走路样式：{name}")
        if name == self._active_walk_variant:
            return
        self._record_user_interaction()
        self._active_walk_variant = name
        self.settings.walk_variant = name
        self._frame_index = 0
        self._render_cache.clear()
        self._mask_cache.clear()
        if self.state is PetState.WALK:
            frames = self._frames_for_state(PetState.WALK)
            if len(frames) > 1:
                self.animation_timer.start(
                    self._frame_interval(PetState.WALK, 0)
                )
            else:
                self.animation_timer.stop()
            self._apply_frame_offset(PetState.WALK)
        self._refresh_pixmap()

    def set_display_height(self, display_height: int) -> None:
        """应用右键菜单尺寸预设，保持窗口底部中心位置并立即重绘。"""

        self._record_user_interaction()
        old_center_x = self.x() + self.width() // 2
        old_bottom = self.y() + self.height()
        self.settings.display_height = max(120, min(600, int(display_height)))
        source = self._pixmaps[PetState.IDLE][0]
        width = round(
            self.settings.display_height * source.width() / source.height()
        )
        self.setFixedSize(width + 12, self.settings.display_height + 14)
        self.label.setGeometry(6, 0, width, self.settings.display_height + 8)
        if self._live2d_view is not None:
            self._live2d_view.setGeometry(self.rect())
        self._render_cache.clear()
        self._mask_cache.clear()
        target = QPoint(
            old_center_x - self.width() // 2,
            old_bottom - self.height(),
        )
        self.move(self._constrained_position(target))
        self._refresh_pixmap()

    def set_wheel_resize_mode(self, mode: str) -> None:
        """切换滚轮缩放触发方式，并清除未完成的高精度滚动累计量。"""

        normalized = str(mode).strip().lower()
        if normalized not in {value for _label, value in WHEEL_RESIZE_MODES}:
            raise ValueError(f"未知滚轮缩放模式：{mode}")
        self.settings.wheel_resize_mode = normalized
        self._wheel_resize_angle_remainder = 0
        self._wheel_resize_pixel_remainder = 0

    def switch_pet_profile(self, pet_id: str) -> None:
        """切换已打包形象，保留屏幕位置和当前显示大小。"""

        profile = next(
            (item for item in self._pet_profiles if item.pet_id == pet_id),
            None,
        )
        if profile is None:
            raise ValueError(f"未知形象：{pet_id}")
        if pet_id == self._active_pet_id:
            return
        if not character_is_approved(
            load_workflow(workflow_path(pet_id=pet_id))
        ):
            raise WorkflowError(f"形象 {pet_id} 的独立角色确认门禁未通过。")

        self._record_user_interaction()
        self._advance_care()
        self._store_care_state()
        self.state_timer.stop()
        self.animation_timer.stop()
        self.extension_timer.stop()
        self._clear_extension_animation()
        self.turn_timer.stop()
        self.interaction_timer.stop()
        self.touch_timer.stop()
        self._touch_active = False
        self._touch_steps.clear()
        self._touch_override_pixmap = None
        self.effect_timer.stop()
        self.look_timer.stop()
        self.keyboard_timer.stop()
        self.focus_timer.stop()
        self._focus_active = False
        self._focus_deadline = 0.0
        self.photo_timer.stop()
        self.photo_bubble.hide()
        self.dialogue_timer.stop()
        self.dialogue_bubble.hide()

        self._active_pet_id = pet_id
        self.settings.pet_id = pet_id
        self._active_manifest_path = profile.manifest_path
        self._pixmaps = self._load_manifest_pixmaps(profile.manifest_path)
        self._restore_care_state()
        self._selfie_photo = self._load_selfie_photo()
        self._render_cache.clear()
        self._mask_cache.clear()
        self.look_timer.setInterval(
            int(self._directional_config.get("poll_interval_ms", 70))
        )
        if self._directional_pixmaps:
            self.look_timer.start()
        self._configure_companion_runtime()
        self.set_display_height(self.settings.display_height)
        self.set_state(PetState.IDLE)
        self._schedule(self.behavior.initial_idle())
        if self._companion_controller is not None:
            on_pet_changed = getattr(
                self._companion_controller,
                "on_pet_changed",
                None,
            )
            if callable(on_pet_changed):
                on_pet_changed()
        if self._break_reminder_controller is not None:
            on_pet_changed = getattr(
                self._break_reminder_controller,
                "on_pet_changed",
                None,
            )
            if callable(on_pet_changed):
                on_pet_changed()

    def choose_custom_display_height(self) -> None:
        """打开像素高度输入框，并应用用户确认的自定义尺寸。"""

        height, accepted = QInputDialog.getInt(
            self,
            "自定义宠物大小",
            "角色高度（120–600 像素）：",
            self.settings.display_height,
            120,
            600,
            10,
        )
        if accepted:
            self.set_display_height(height)

    def return_to_primary_screen(self) -> None:
        """将宠物重新放到主屏幕右下角。"""

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        _minimum_x, maximum_x, _minimum_y, maximum_y = (
            self._visible_position_limits(area)
        )
        self.move(maximum_x - 24, maximum_y - 12)

    def trigger_interaction(self) -> None:
        """结合当前情绪数值触发友好表情或挥手反馈。"""

        if self.dragging:
            return
        self._record_user_interaction()
        if self.mood.energy < 30:
            state = PetState.SLEEPY
        elif self.mood.boredom > 70:
            state = PetState.CURIOUS
        else:
            state = random.choice((PetState.WAVE, PetState.HAPPY, PetState.SHY))
        self._show_emotion(
            state,
            1800,
            dialogue_action=state.value,
            dialogue_source="manual",
            force_dialogue=True,
        )

    def trigger_manual_emotion(
        self,
        state_name: object,
        duration_ms: int = 1800,
    ) -> bool:
        """从角色清单声明的菜单触发一个已装配基础情绪动作。"""

        allowed = {
            state.value: state
            for state in (
                PetState.WAVE,
                PetState.HAPPY,
                PetState.SHY,
                PetState.SURPRISED,
                PetState.ANNOYED,
                PetState.SLEEPY,
                PetState.CURIOUS,
            )
        }
        state = allowed.get(str(state_name).strip().lower())
        if state is None or self.dragging:
            return False
        self._record_user_interaction()
        self._show_emotion(
            state,
            min(5000, max(500, int(duration_ms))),
            dialogue_action=state.value,
            dialogue_source="manual",
            force_dialogue=True,
        )
        return True

    def trigger_declared_manual_action(
        self,
        sequence_name: object,
        state_name: object,
        duration_ms: int = 1800,
    ) -> bool:
        """优先播放清单声明的扩展序列，不可用时降级到合法基础情绪。"""

        sequence = str(sequence_name).strip()
        if sequence and self.trigger_extension_action(sequence):
            return True
        return self.trigger_manual_emotion(state_name, duration_ms)

    def _show_emotion(
        self,
        state: PetState,
        duration_ms: int = 1600,
        *,
        dialogue_action: str | None = None,
        dialogue_source: str | None = None,
        force_dialogue: bool = False,
    ) -> None:
        """显示一次短暂互动表情，并在计时结束后恢复自主生活。"""

        resolved_state = self._state_fallbacks.get(state, state)
        if dialogue_action == state.value:
            dialogue_action = resolved_state.value
        self._cancel_touch_sequence(restore_idle=False)
        self.state_timer.stop()
        self.set_state(resolved_state)
        self.interaction_timer.start(max(500, duration_ms))
        if dialogue_action and dialogue_source:
            self._maybe_show_dialogue(
                dialogue_action,
                source=dialogue_source,
                force=force_dialogue,
            )

    def _choose_touch_branch(self) -> PetState:
        """按固定、轮换或权重策略选择害羞/开心，默认不连续重复。"""

        policy = str(
            self._touch_config.get("policy")
            or self._touch_config.get("default_policy")
            or "weighted_no_repeat"
        )
        if policy == "fixed_shy":
            branch = PetState.SHY
        elif policy == "fixed_happy":
            branch = PetState.HAPPY
        elif policy == "alternate":
            branch = (
                PetState.HAPPY
                if self._last_touch_branch is PetState.SHY
                else PetState.SHY
            )
        else:
            weights = self._touch_config.get(
                "branch_weights",
                {"shy": 0.55, "happy": 0.45},
            )
            if not isinstance(weights, dict):
                weights = {"shy": 0.55, "happy": 0.45}
            candidates = [PetState.SHY, PetState.HAPPY]
            candidate_weights = [
                max(0.0, float(weights.get("shy", 0.55))),
                max(0.0, float(weights.get("happy", 0.45))),
            ]
            if (
                policy == "weighted_no_repeat"
                and self._last_touch_branch in candidates
            ):
                index = candidates.index(self._last_touch_branch)
                candidates.pop(index)
                candidate_weights.pop(index)
            branch = random.choices(
                candidates,
                weights=candidate_weights,
                k=1,
            )[0]
        self._last_touch_branch = branch
        return branch

    @staticmethod
    def _blend_pixmaps(
        first: QPixmap,
        second: QPixmap,
        second_opacity: float = 0.55,
    ) -> QPixmap:
        """用两个已验收帧生成内存过渡，供旧宠物无专用帧时降级。"""

        width = max(first.width(), second.width())
        height = max(first.height(), second.height())
        result = QPixmap(width, height)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.drawPixmap((width - first.width()) // 2, height - first.height(), first)
        painter.setOpacity(min(1.0, max(0.0, second_opacity)))
        painter.drawPixmap(
            (width - second.width()) // 2,
            height - second.height(),
            second,
        )
        painter.end()
        return result

    def _start_touch_sequence(self) -> bool:
        """启动“惊讶→害羞/开心→过渡→待机”的独占摸头序列。"""

        if self.dragging:
            return False
        now = time.monotonic()
        throttle_ms = int(self._touch_config.get("throttle_ms", 1400))
        if self._touch_active:
            # 默认行为明确为忽略，避免重复点击堆叠状态。
            return False
        if (now - self._last_touch_started_at) * 1000 < max(0, throttle_ms):
            return False
        self._last_touch_started_at = now
        self._touch_active = True
        self._record_user_interaction()
        self.state_timer.stop()
        self.interaction_timer.stop()
        self.hover_timer.stop()
        branch = self._choose_touch_branch()
        durations = self._touch_config.get("durations_ms", {})
        if not isinstance(durations, dict):
            durations = {}
        surprised_ms = int(durations.get("surprised", 320))
        reaction_ms = int(durations.get("reaction", 760))
        transition_ms = int(durations.get("transition", 180))
        idle_blend_ms = int(durations.get("idle_blend", 160))
        self._touch_steps.clear()
        self._touch_steps.extend(
            (
                ("state", PetState.SURPRISED, max(120, surprised_ms)),
                ("state", branch, max(300, reaction_ms)),
            )
        )
        transitions = list(self._touch_transition_pixmaps)
        if not transitions:
            transitions = [
                self._blend_pixmaps(
                    self._pixmaps[branch][0],
                    self._pixmaps[PetState.IDLE][0],
                )
            ]
        for pixmap in transitions[:2]:
            self._touch_steps.append(
                ("pixmap", pixmap, max(80, transition_ms))
            )
        self._touch_steps.append(
            ("state", PetState.IDLE, max(80, idle_blend_ms))
        )
        self._advance_touch_sequence()
        self._maybe_show_dialogue("touch", source="touch")
        return True

    def _advance_touch_sequence(self) -> None:
        """推进一个摸头反馈步骤，并在结束后恢复自主状态机。"""

        if not self._touch_active or self.dragging:
            return
        if not self._touch_steps:
            self._touch_active = False
            self._touch_override_pixmap = None
            if self._focus_active:
                self.set_state(PetState.SIT)
            else:
                self._schedule(self.behavior.initial_idle())
            return
        kind, payload, duration_ms = self._touch_steps.popleft()
        if kind == "state":
            self._touch_override_pixmap = None
            self.set_state(payload)
        else:
            self.animation_timer.stop()
            self.effect_timer.stop()
            self._touch_override_pixmap = payload
            self._refresh_pixmap()
        self.touch_timer.start(duration_ms)

    def _cancel_touch_sequence(self, *, restore_idle: bool = True) -> None:
        """清空摸头序列；拖拽或其他显式互动可安全抢占。"""

        if not self._touch_active and self._touch_override_pixmap is None:
            return
        self.touch_timer.stop()
        self._touch_steps.clear()
        self._touch_active = False
        self._touch_override_pixmap = None
        if restore_idle and not self.dragging:
            self.set_state(PetState.IDLE)

    def trigger_selfie(self) -> None:
        """显式播放一次举起相机、闪光和查看照片的自拍序列。"""

        if self.dragging:
            return
        self._record_user_interaction()
        self._show_emotion(
            PetState.SELFIE,
            2600,
            dialogue_action="selfie",
            dialogue_source="manual",
            force_dialogue=True,
        )

    def _interaction_zone(self, point: QPoint) -> str:
        """按当前透明轮廓自适应划分头部、脸部、身体和相机区域。"""

        if self._is_head_hit(point):
            return "head"
        bounds = self.mask().boundingRect()
        if bounds.isEmpty():
            bounds = self.label.geometry()
        x = (point.x() - bounds.left()) / max(1, bounds.width())
        y = (point.y() - bounds.top()) / max(1, bounds.height())
        if y < 0.46:
            return "face"
        if 0.42 <= y <= 0.82 and x < 0.43:
            return "camera"
        return "body"

    def _is_head_hit(self, point: QPoint) -> bool:
        """用缩放后的 alpha 区域顶部窄带判定头部，避免身体误触。"""

        visible = self.mask()
        bounds = visible.boundingRect()
        if bounds.isEmpty() or not visible.contains(point):
            return False
        head_height = max(
            1,
            round(min(bounds.height() * 0.27, bounds.width() * 0.55)),
        )
        head_band = QRegion(
            QRect(
                bounds.left(),
                bounds.top(),
                bounds.width(),
                head_height,
            )
        )
        return visible.intersected(head_band).contains(point)

    def _handle_click(self, point: QPoint) -> None:
        """根据点击区域更新情绪并选择对应反馈。"""

        zone = self._interaction_zone(point)
        self._record_user_interaction()
        if zone == "camera":
            self.trigger_selfie()
            return
        if zone == "head":
            if self._start_touch_sequence():
                self._receive_affection()
            return
        if zone == "face":
            self.mood.receive_poke(False)
            self._show_emotion(
                PetState.SURPRISED,
                1300,
                dialogue_action="surprised",
                dialogue_source="touch",
            )
            return

        now = time.monotonic()
        self._poke_times.append(now)
        while self._poke_times and now - self._poke_times[0] > 2.5:
            self._poke_times.popleft()
        repeated = len(self._poke_times) >= 3
        self.mood.receive_poke(repeated)
        self._show_emotion(
            PetState.ANNOYED if repeated else PetState.SURPRISED,
            1800 if repeated else 1200,
            dialogue_action="annoyed" if repeated else "surprised",
            dialogue_source="touch",
        )

    def _track_passive_motion(self, point: QPoint) -> None:
        """跟踪无按键悬停；停留触发好奇，头部往返移动判定为摸头。"""

        zone = self._interaction_zone(point)
        self._hover_zone = zone
        if self.state is PetState.IDLE and not self.interaction_timer.isActive():
            self.hover_timer.start(700)
        if zone != "head":
            self._stroke_points.clear()
            return

        now = time.monotonic()
        self._stroke_points.append((now, point))
        while self._stroke_points and now - self._stroke_points[0][0] > 1.2:
            self._stroke_points.popleft()
        distance = sum(
            (current[1] - previous[1]).manhattanLength()
            for previous, current in zip(
                self._stroke_points,
                list(self._stroke_points)[1:],
            )
        )
        if distance >= 70 and now - self._last_stroke_reaction >= 2.0:
            self._last_stroke_reaction = now
            self._stroke_points.clear()
            self._receive_affection()
            self._record_user_interaction()
            state = PetState.SHY if self.mood.affinity >= 70 else PetState.HAPPY
            self._show_emotion(
                state,
                1600,
                dialogue_action="touch",
                dialogue_source="touch",
            )

    def _trigger_hover_curiosity(self) -> None:
        """鼠标在宠物附近稳定停留时显示好奇注视。"""

        if (
            self._hover_zone
            and self.state is PetState.IDLE
            and not self.dragging
            and not self._press_pending
            and not self.interaction_timer.isActive()
        ):
            self._record_user_interaction()
            self._show_emotion(
                PetState.CURIOUS,
                1300,
                dialogue_action="curious",
                dialogue_source="autonomous",
            )

    def _show_dialogue_text(self, text: str, *, force: bool = False) -> None:
        """显示纯文本气泡；可强制绕过随机台词开关，自拍仍优先。"""

        if (
            not text
            or (not force and not self._dialogue_config.get("enabled", False))
            or self.photo_bubble.isVisible()
        ):
            return
        self.dialogue_bubble.setText(text)
        self.dialogue_bubble.adjustSize()
        self._position_dialogue_bubble()
        self.dialogue_bubble.show()
        self.dialogue_bubble.raise_()
        self.dialogue_timer.start(
            int(self._dialogue_config["bubble_duration_ms"])
        )

    def _position_dialogue_bubble(self) -> None:
        """优先把文字气泡放在角色上方，空间不足时改放侧边。"""

        if not hasattr(self, "dialogue_bubble"):
            return
        area = self._screen_geometry()
        visible_bounds = self.mask().boundingRect()
        if visible_bounds.isEmpty():
            visible_bounds = self.rect()
        character_left = self.x() + visible_bounds.left()
        character_right = self.x() + visible_bounds.right() + 1
        character_top = self.y() + visible_bounds.top()
        character_center_x = (character_left + character_right) // 2
        gap = 9
        x = character_center_x - self.dialogue_bubble.width() // 2
        y = character_top - self.dialogue_bubble.height() - gap
        if area is not None:
            if y < area.top():
                x = character_right + gap
                y = self.y() + max(
                    0,
                    (self.height() - self.dialogue_bubble.height()) // 2,
                )
                if x + self.dialogue_bubble.width() > area.right() + 1:
                    x = character_left - self.dialogue_bubble.width() - gap
            x = min(
                max(x, area.left()),
                area.right() - self.dialogue_bubble.width() + 1,
            )
            y = min(
                max(y, area.top()),
                area.bottom() - self.dialogue_bubble.height() + 1,
            )
        self.dialogue_bubble.move(x, y)

    def preview_dialogue(self) -> None:
        """忽略冷却，从当前角色台词池随机预览一句。"""

        actions = list(self._dialogue_config.get("lines", {}))
        if not actions:
            return
        action = random.choice(actions)
        self._maybe_show_dialogue(
            action,
            source="manual",
            force=True,
            ignore_cooldown=True,
        )

    def edit_dialogue_lines(self, action: str) -> bool:
        """用每行一句的输入框编辑当前宠物某个动作的本地台词。"""

        labels = self._dialogue_config.get("action_labels", {})
        label = str(labels.get(action, action))
        current = self._dialogue_selector.lines_for(action)
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "编辑聊天词",
            f"{label}（每行一句，留空可关闭该动作台词）：",
            "\n".join(current),
        )
        if not accepted:
            return False
        lines = sanitize_dialogue_lines(
            text.splitlines(),
            max_length=int(self._dialogue_config["max_text_length"]),
            max_lines=int(self._dialogue_config["max_lines_per_action"]),
        )
        pet_overrides = self.settings.dialogue_overrides.setdefault(
            self._dialogue_pet_id,
            {},
        )
        pet_overrides[action] = lines
        self._reset_dialogue_selector()
        if lines:
            self._show_dialogue_text(lines[0])
        else:
            self.dialogue_bubble.hide()
        return True

    def reset_dialogue_lines(self) -> None:
        """删除当前宠物全部用户覆盖并恢复角色默认台词。"""

        self.settings.dialogue_overrides.pop(self._dialogue_pet_id, None)
        self._reset_dialogue_selector()
        self._maybe_show_dialogue(
            "idle",
            source="manual",
            force=True,
            ignore_cooldown=True,
        )

    def _show_photo_bubble(self) -> None:
        """在宠物旁显示独立自拍成片，并在数秒后自动隐藏。"""

        if self._selfie_photo.isNull():
            return
        self.dialogue_timer.stop()
        self.dialogue_bubble.hide()
        ratio = max(1.0, self.devicePixelRatioF())
        photo = self._scaled_selfie_photo(ratio)
        logical_size = QSize(
            max(1, round(photo.width() / ratio)),
            max(1, round(photo.height() / ratio)),
        )
        self.photo_bubble.setPixmap(photo)
        self.photo_bubble.setFixedSize(logical_size)
        area = self._screen_geometry()
        visible_bounds = self.mask().boundingRect()
        if visible_bounds.isEmpty():
            character_left = self.x()
            character_right = self.x() + self.width()
        else:
            character_left = self.x() + visible_bounds.left()
            character_right = self.x() + visible_bounds.right() + 1
        gap = 8
        x = character_left - self.photo_bubble.width() - gap
        if area is not None and x < area.left():
            x = character_right + gap
        y = self.y() + max(0, (self.height() - self.photo_bubble.height()) // 2)
        self.photo_bubble.move(x, y)
        self.photo_bubble.show()
        self.photo_timer.start(3800)

    def _scaled_selfie_photo(self, ratio: float) -> QPixmap:
        """按设备像素比生成照片缩略图，避免高 DPI 屏幕二次放大导致模糊。"""

        if self._selfie_photo.isNull():
            return QPixmap()
        ratio = max(1.0, ratio)
        photo = self._selfie_photo.scaled(
            max(1, round(150 * ratio)),
            max(1, round(210 * ratio)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        photo.setDevicePixelRatio(ratio)
        return photo

    def _finish_interaction(self) -> None:
        """结束互动并恢复自主待机。"""

        if not self.dragging:
            if self.state is PetState.SELFIE:
                self._show_photo_bubble()
            if self._focus_active:
                self.set_state(PetState.SIT)
            else:
                self._schedule(self.behavior.initial_idle())

    def _build_context_menu(self) -> QMenu:
        """构建按标签和真实动作目标去重、且每项均可执行的右键菜单。"""

        menu = QMenu(self)
        pause_action = QAction("恢复跑动" if self.paused else "暂停跑动", self)
        pause_action.triggered.connect(lambda: self.set_paused(not self.paused))
        menu.addAction(pause_action)

        actions_menu = QMenu("互动动作", menu)
        menu.addMenu(actions_menu)
        interact_action = QAction("打个招呼", self)
        interact_action.triggered.connect(self.trigger_interaction)
        actions_menu.addAction(interact_action)
        selfie_action = QAction("自拍一下", self)
        selfie_action.triggered.connect(self.trigger_selfie)
        actions_menu.addAction(selfie_action)
        shown_action_labels = {"打个招呼", "自拍一下"}
        shown_action_targets = {"builtin:interaction", "builtin:selfie"}
        signature = self._companion_section("signature_action")
        signature_sequence = ""
        if signature.get("enabled", False):
            signature_sequence = str(signature.get("sequence", "")).strip()
            signature_label = str(signature.get("label") or "招牌动作").strip()
            signature_state = str(signature.get("state", "wave")).strip().lower()
            signature_target = (
                f"extension:{signature_sequence}"
                if signature_sequence in self._extension_animations
                else f"state:{signature_state or 'wave'}"
            )
            if (
                signature_label
                and signature_label not in shown_action_labels
                and signature_target not in shown_action_targets
            ):
                signature_action = QAction(signature_label, self)
                signature_action.triggered.connect(
                    lambda _checked=False: self.trigger_signature_action()
                )
                actions_menu.addAction(signature_action)
                shown_action_labels.add(signature_label)
                shown_action_targets.add(signature_target)
        for name, config in self._extension_animation_config.items():
            raw_menu_label = config.get("menu_label")
            menu_label = str(raw_menu_label).strip() if raw_menu_label else ""
            target = f"extension:{name}"
            if (
                not menu_label
                or menu_label in shown_action_labels
                or target in shown_action_targets
            ):
                continue
            extension_action = QAction(menu_label, self)
            extension_action.triggered.connect(
                lambda _checked=False, value=name: (
                    self.trigger_extension_action(value)
                )
            )
            actions_menu.addAction(extension_action)
            shown_action_labels.add(menu_label)
            shown_action_targets.add(target)
        manual_actions = self._companion_config.get("manual_actions", [])
        if isinstance(manual_actions, list):
            for raw_action in manual_actions:
                if not isinstance(raw_action, dict):
                    continue
                raw_label = raw_action.get("label")
                label = str(raw_label).strip() if raw_label else ""
                sequence_name = str(raw_action.get("sequence", "")).strip()
                state_name = str(raw_action.get("state", "")).strip().lower()
                if sequence_name in self._extension_animations:
                    target = f"extension:{sequence_name}"
                elif state_name in {
                    PetState.WAVE.value,
                    PetState.HAPPY.value,
                    PetState.SHY.value,
                    PetState.SURPRISED.value,
                    PetState.ANNOYED.value,
                    PetState.SLEEPY.value,
                    PetState.CURIOUS.value,
                }:
                    target = f"state:{state_name}"
                else:
                    continue
                if (
                    not label
                    or label in shown_action_labels
                    or target in shown_action_targets
                ):
                    continue
                duration_ms = int(raw_action.get("duration_ms", 1800))
                manual_action = QAction(label, self)
                manual_action.triggered.connect(
                    lambda _checked=False,
                    sequence=sequence_name,
                    state=state_name,
                    duration=duration_ms: (
                        self.trigger_declared_manual_action(
                            sequence,
                            state,
                            duration,
                        )
                    )
                )
                actions_menu.addAction(manual_action)
                shown_action_labels.add(label)
                shown_action_targets.add(target)
        if self._dialogue_config.get("enabled", False):
            dialogue_menu = QMenu("聊天词", menu)
            menu.addMenu(dialogue_menu)
            preview_action = QAction("随机说一句", self)
            preview_action.triggered.connect(self.preview_dialogue)
            dialogue_menu.addAction(preview_action)
            edit_menu = QMenu("编辑聊天词", dialogue_menu)
            dialogue_menu.addMenu(edit_menu)
            labels = self._dialogue_config.get("action_labels", {})
            for action_name in self._dialogue_config.get("lines", {}):
                edit_action = QAction(
                    str(labels.get(action_name, action_name)),
                    self,
                )
                edit_action.triggered.connect(
                    lambda _checked=False, value=action_name: (
                        self.edit_dialogue_lines(value)
                    )
                )
                edit_menu.addAction(edit_action)
            dialogue_menu.addSeparator()
            reset_action = QAction("恢复默认聊天词", self)
            reset_action.triggered.connect(self.reset_dialogue_lines)
            dialogue_menu.addAction(reset_action)
        cursor = self._companion_section("cursor_chase")
        if cursor.get("enabled", False):
            cursor_action = QAction(
                "停止追逐鼠标"
                if self._cursor_chase_enabled
                else "追逐并抱住鼠标",
                self,
            )
            cursor_action.setCheckable(True)
            cursor_action.setChecked(self._cursor_chase_enabled)
            cursor_action.triggered.connect(self.set_cursor_chase)
            menu.addAction(cursor_action)
        movement_region_menu = QMenu("活动区域", menu)
        menu.addMenu(movement_region_menu)
        movement_region_action = QAction("启用区域限制", self)
        movement_region_action.setCheckable(True)
        movement_region_action.setChecked(
            self.settings.movement_region_enabled
        )
        movement_region_action.triggered.connect(
            self.set_movement_region_enabled
        )
        movement_region_menu.addAction(movement_region_action)
        choose_region_action = QAction("设置活动区域…", self)
        choose_region_action.triggered.connect(self.choose_movement_region)
        movement_region_menu.addAction(choose_region_action)
        movement_region_menu.addSeparator()
        configured_region = self._configured_movement_region()
        if configured_region is None:
            region_label = "首次启用：围绕当前位置 800×500"
        else:
            x, y, width, height = configured_region.getRect()
            region_label = f"当前：({x}, {y}) · {width}×{height}"
        region_info_action = QAction(region_label, self)
        region_info_action.setEnabled(False)
        movement_region_menu.addAction(region_info_action)
        focus = self._companion_section("focus_session")
        if focus.get("enabled", False):
            if self._focus_active:
                remaining_minutes = max(
                    1,
                    math.ceil(self.focus_remaining_ms() / 60000),
                )
                focus_action = QAction(
                    f"结束专注（剩余约 {remaining_minutes} 分钟）",
                    self,
                )
                focus_action.triggered.connect(
                    lambda _checked=False: self.cancel_focus_session()
                )
            else:
                minutes = max(
                    1,
                    round(float(focus.get("duration_minutes", 25))),
                )
                focus_action = QAction(f"开始 {minutes} 分钟专注", self)
                focus_action.triggered.connect(
                    lambda _checked=False: self.start_focus_session()
                )
            menu.addAction(focus_action)
        mood_action = QAction(
            "心情："
            f"亲密 {self.mood.affinity} · "
            f"精力 {self.mood.energy} · "
            f"无聊 {self.mood.boredom}",
            self,
        )
        mood_action.setEnabled(False)
        menu.addAction(mood_action)
        if self._care_enabled():
            self._advance_care()
            self._store_care_state()
            care_menu = QMenu("养成", menu)
            menu.addMenu(care_menu)
            level_action = QAction(
                f"等级 {self.care.level} · 金币 {self.care.coins}",
                self,
            )
            level_action.setEnabled(False)
            care_menu.addAction(level_action)
            needs_action = QAction(self.care.summary(), self)
            needs_action.setEnabled(False)
            care_menu.addAction(needs_action)
            health_action = QAction(
                f"健康 {round(self.care.health)} · "
                f"好感 {round(self.care.bond)} · "
                f"经验 {round(self.care.experience)}",
                self,
            )
            health_action.setEnabled(False)
            care_menu.addAction(health_action)
            care_menu.addSeparator()
            feed_cost = int(self._care_config.get("feed_cost", 8))
            feed_action = QAction(f"喂小点心（{feed_cost} 金币）", self)
            feed_action.triggered.connect(
                lambda _checked=False: self.care_feed()
            )
            care_menu.addAction(feed_action)
            drink_cost = int(self._care_config.get("drink_cost", 2))
            drink_action = QAction(f"喝水（{drink_cost} 金币）", self)
            drink_action.triggered.connect(
                lambda _checked=False: self.care_drink()
            )
            care_menu.addAction(drink_action)
            play_action = QAction("一起玩", self)
            play_action.triggered.connect(
                lambda _checked=False: self.care_play()
            )
            care_menu.addAction(play_action)
            rest_action = QAction("休息一下", self)
            rest_action.triggered.connect(
                lambda _checked=False: self.care_rest()
            )
            care_menu.addAction(rest_action)
        if self._companion_controller is not None:
            populate_menu = getattr(
                self._companion_controller,
                "populate_menu",
                None,
            )
            if callable(populate_menu):
                menu.addSeparator()
                populate_menu(menu)
        if self._break_reminder_controller is not None:
            populate_menu = getattr(
                self._break_reminder_controller,
                "populate_menu",
                None,
            )
            if callable(populate_menu):
                menu.addSeparator()
                populate_menu(menu)
        if len(self._pet_profiles) > 1:
            profile_menu = menu.addMenu("切换形象")
            for profile in self._pet_profiles:
                profile_action = QAction(profile.label, self)
                profile_action.setCheckable(True)
                profile_action.setChecked(profile.pet_id == self._active_pet_id)
                profile_action.triggered.connect(
                    lambda _checked=False, value=profile.pet_id: (
                        self.switch_pet_profile(value)
                    )
                )
                profile_menu.addAction(profile_action)
        if len(self._walk_variant_pixmaps) > 1:
            walk_menu = QMenu("走路样式", menu)
            menu.addMenu(walk_menu)
            for name, label in self._walk_variant_labels.items():
                walk_action = QAction(label, self)
                walk_action.setCheckable(True)
                walk_action.setChecked(name == self._active_walk_variant)
                walk_action.triggered.connect(
                    lambda _checked=False, value=name: (
                        self.set_walk_variant(value)
                    )
                )
                walk_menu.addAction(walk_action)
        size_menu = menu.addMenu("宠物大小")
        for label, height in DISPLAY_SIZE_PRESETS:
            size_action = QAction(label, self)
            size_action.setCheckable(True)
            size_action.setChecked(self.settings.display_height == height)
            size_action.triggered.connect(
                lambda _checked=False, value=height: self.set_display_height(value)
            )
            size_menu.addAction(size_action)
        size_menu.addSeparator()
        custom_size_action = QAction(
            f"自定义大小…（当前 {self.settings.display_height}）",
            self,
        )
        custom_size_action.triggered.connect(self.choose_custom_display_height)
        size_menu.addAction(custom_size_action)
        wheel_resize_menu = size_menu.addMenu("滚轮调节大小")
        for label, mode in WHEEL_RESIZE_MODES:
            wheel_action = QAction(label, self)
            wheel_action.setCheckable(True)
            wheel_action.setChecked(self.settings.wheel_resize_mode == mode)
            wheel_action.triggered.connect(
                lambda _checked=False, value=mode: (
                    self.set_wheel_resize_mode(value)
                )
            )
            wheel_resize_menu.addAction(wheel_action)
        return_action = QAction("回到主屏幕", self)
        return_action.triggered.connect(self.return_to_primary_screen)
        menu.addAction(return_action)
        hide_action = QAction("隐藏", self)
        hide_action.triggered.connect(self.hide)
        menu.addAction(hide_action)
        menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """在鼠标位置显示窗口菜单。"""

        self._record_user_interaction()
        self._build_context_menu().exec(event.globalPos())

    def wheelEvent(self, event: QWheelEvent) -> None:
        """按用户选择的模式累计滚轮输入，并以稳定步长缩放桌宠。"""

        mode = self.settings.wheel_resize_mode
        if mode == "disabled":
            super().wheelEvent(event)
            return
        if (
            mode == "ctrl"
            and not event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            super().wheelEvent(event)
            return

        angle_y = event.angleDelta().y()
        if angle_y:
            total = self._wheel_resize_angle_remainder + angle_y
            steps = math.trunc(total / 120)
            self._wheel_resize_angle_remainder = total - steps * 120
        else:
            pixel_y = event.pixelDelta().y()
            total = self._wheel_resize_pixel_remainder + pixel_y
            steps = math.trunc(total / 30)
            self._wheel_resize_pixel_remainder = total - steps * 30

        if steps:
            self.set_display_height(
                self.settings.display_height + steps * WHEEL_RESIZE_STEP_PX
            )
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """记录左键按下；只有移动超过系统阈值后才真正进入拖拽。"""

        if event.button() == Qt.MouseButton.LeftButton:
            self._record_user_interaction()
            self._press_pending = True
            self.dragging = False
            self.state_timer.stop()
            self.interaction_timer.stop()
            self.hover_timer.stop()
            self._press_local = event.position().toPoint()
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """拖动期间根据全局鼠标位置移动并限制窗口。"""

        if event.buttons() & Qt.MouseButton.LeftButton:
            current_global = event.globalPosition().toPoint()
            if (
                self._press_pending
                and (current_global - self._press_global).manhattanLength()
                >= QApplication.startDragDistance()
            ):
                self._press_pending = False
                self.dragging = True
                self._cancel_touch_sequence(restore_idle=False)
                self.mood.receive_drag()
                self.set_state(PetState.DRAG)
            if not self.dragging:
                event.accept()
                return
            target = event.globalPosition().toPoint() - self._drag_offset
            self.move(self._constrained_position(target))
            event.accept()
            return
        self._track_passive_motion(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """左键释放时结束拖动并恢复待机。"""

        if event.button() == Qt.MouseButton.LeftButton:
            if self.dragging:
                self.dragging = False
                self._press_pending = False
                self._show_emotion(
                    PetState.SURPRISED,
                    1100,
                    dialogue_action="surprised",
                    dialogue_source="touch",
                )
            elif self._press_pending:
                self._press_pending = False
                self._handle_click(self._press_local)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        """鼠标离开宠物时取消尚未触发的悬停和摸头轨迹。"""

        self._hover_zone = ""
        self._stroke_points.clear()
        self.hover_timer.stop()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击左键优先打开快捷聊天，不可用时回退到本地反馈。"""

        if event.button() == Qt.MouseButton.LeftButton:
            self._cancel_touch_sequence(restore_idle=False)
            self.dragging = False
            self._press_pending = False
            self._record_user_interaction()
            if bool(self._companion_ui_config.get("double_click_chat", True)):
                controller = self._companion_controller
                compact = bool(
                    self._companion_ui_config.get("compact_chat", True)
                )
                callback = getattr(
                    controller,
                    "open_compact_chat" if compact else "open_center",
                    None,
                )
                if callable(callback):
                    try:
                        if compact:
                            callback()
                        else:
                            callback("chat")
                    except Exception:
                        # 陪伴存储或界面不可用时保留基础桌宠互动，不让双击崩溃。
                        pass
                    else:
                        event.accept()
                        return
            self._receive_affection()
            self._show_emotion(
                PetState.HAPPY,
                1800,
                dialogue_action="happy",
                dialogue_source="manual",
                force_dialogue=True,
            )
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
