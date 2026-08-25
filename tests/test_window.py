"""
本模块验证桌面宠物窗口的连续帧控制、表情符号、轮廓遮罩、DPI 渲染缓存、分区互动、
自拍成片，以及滚轮缩放设置、可切换活动区域、本地台词气泡、稳定追鼠标、陪打字、专注庆祝、招牌动作、可选走路样式、
扩展序列、按动作目标去重且可真实触发的互动菜单、空闲随机单帧微表情、八向视线、
可选养成模拟、版本化跳跃复用和形象切换陪伴通知等通用陪伴行为。

测试在 Qt 的离屏平台中创建真实 PetWindow，但不显示到用户桌面、不写配置文件，
也不启动系统托盘。重点检查透明区域不会形成完整矩形点击区、Live2D 根运动及画布
留白差异不会被静态精灵遮罩裁切、重复绘制能够复用缓存，以及坐下过渡可正向停在末帧并反向
回到站立帧；拖拽边界必须依据角色可见轮廓而不是完整透明画布，且不受自主活动矩形限制。
"""

from __future__ import annotations

import os
import json
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QRegion, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QLabel

from onepic_desktop_pet.behavior import PetState, StateDecision
from onepic_desktop_pet.care import normalize_care_config
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.dialogue import normalize_dialogue_config
from onepic_desktop_pet.emotion_effects import emotion_effect_name
from onepic_desktop_pet.window import (
    DISPLAY_SIZE_PRESETS,
    WHEEL_RESIZE_MODES,
    PetWindow,
)


def _create_window() -> tuple[QApplication, PetWindow]:
    """创建或复用离屏 Qt 应用，并返回采用默认设置的宠物窗口。"""

    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings())
    window.show()
    app.processEvents()
    return app, window


def test_double_click_opens_compact_chat_before_local_reaction() -> None:
    """左键双击应直达快捷聊天，不再只播放开心表情。"""

    app, window = _create_window()
    opened: list[str] = []

    class _CompanionController:
        def open_compact_chat(self) -> None:
            opened.append("compact")

    class _DoubleClickEvent:
        accepted = False

        @staticmethod
        def button() -> Qt.MouseButton:
            return Qt.MouseButton.LeftButton

        def accept(self) -> None:
            self.accepted = True

    window.attach_companion_controller(_CompanionController())
    event = _DoubleClickEvent()
    before_affinity = window.mood.affinity
    window.mouseDoubleClickEvent(event)  # type: ignore[arg-type]

    assert opened == ["compact"]
    assert event.accepted is True
    assert window.mood.affinity == before_affinity
    window.close()
    window.deleteLater()
    app.processEvents()


def test_real_qt_double_click_event_reaches_compact_chat_controller() -> None:
    """用 Qt 真实双击序列验证透明窗口事件链，而非只直接调用处理函数。"""

    app, window = _create_window()
    opened: list[str] = []

    class _CompanionController:
        def open_compact_chat(self) -> None:
            opened.append("compact")

    window.attach_companion_controller(_CompanionController())
    QTest.mouseDClick(
        window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        window.rect().center(),
        5,
    )
    app.processEvents()

    assert opened == ["compact"]
    window.close()
    window.deleteLater()
    app.processEvents()


def _enable_companion_behaviors(window: PetWindow) -> None:
    """为离屏测试注入可复用的旗舰陪伴配置。"""

    window._companion_config = {
        "cursor_chase": {
            "enabled": True,
            "default_enabled": False,
            "speed_pixels_per_second": 200,
            "hug_distance_px": 60,
            "release_distance_px": 110,
            "facing_dead_zone_px": 22,
            "slowdown_distance_px": 180,
            "minimum_speed_ratio": 0.28,
        },
        "keyboard_companion": {
            "enabled": True,
            "burst_window_ms": 1500,
            "burst_key_count": 3,
            "reaction_cooldown_ms": 1000,
            "reaction_state": "wave",
        },
        "focus_session": {
            "enabled": True,
            "duration_minutes": 25,
            "celebration_state": "happy",
        },
        "signature_action": {
            "enabled": True,
            "label": "招牌动作",
            "state": "wave",
            "duration_ms": 1800,
        },
        "manual_actions": [
            {"label": "好奇看看", "state": "curious", "duration_ms": 1700},
        ],
    }


def test_care_simulation_is_opt_in_and_uses_existing_state() -> None:
    """养成层关闭时不推进，开启后可按现有走路状态消耗活力。"""

    app = QApplication.instance() or QApplication([])
    disabled = PetWindow(PetSettings())
    disabled_before = disabled.care.vitality
    disabled._advance_care(disabled._last_care_update + 600)
    assert disabled.care.vitality == disabled_before

    enabled = PetWindow(PetSettings(care_simulation_enabled=True))
    enabled.set_state(PetState.WALK)
    enabled_before = enabled.care.vitality
    enabled._advance_care(enabled._last_care_update + 600)
    assert enabled.care.vitality < enabled_before
    assert enabled._active_care() is enabled.care

    disabled.close()
    disabled.deleteLater()
    enabled.close()
    enabled.deleteLater()
    app.processEvents()


def test_care_actions_persist_by_pet_and_reuse_existing_animations() -> None:
    """喂食、饮水、玩耍与专注奖励不得要求新的角色图片。"""

    app = QApplication.instance() or QApplication([])
    settings = PetSettings(care_simulation_enabled=True)
    window = PetWindow(settings)
    window._care_config = {
        "enabled": True,
        "feed_cost": 8,
        "feed_amount": 24,
        "drink_cost": 2,
        "drink_amount": 28,
        "play_vitality_cost": 6,
        "play_happiness_gain": 14,
        "focus_reward_coins": 18,
        "focus_reward_experience": 10,
        "action_cooldown_ms": 200,
    }
    window.care.satiety = 30
    window.care.hydration = 30
    window.care.coins = 20

    assert window.care_feed() is True
    window._last_care_action_at = 0
    assert window.care_drink() is True
    window._last_care_action_at = 0
    assert window.care_play() is True
    assert window.care.satiety > 30
    assert window.care.hydration > 30
    assert window._care_pet_id() in settings.care_states

    _enable_companion_behaviors(window)
    coins_before = window.care.coins
    assert window.start_focus_session(duration_ms=5000) is True
    window._complete_focus_session()
    assert window.care.coins == coins_before + 18

    window.close()
    window.deleteLater()
    app.processEvents()


def test_window_uses_character_mask_and_reuses_render_cache() -> None:
    app, window = _create_window()
    initial_render_count = len(window._render_cache)
    initial_mask_count = len(window._mask_cache)

    window._refresh_pixmap()

    assert not window.mask().isEmpty()
    assert window.mask().boundingRect().width() < window.width()
    assert len(window._render_cache) == initial_render_count
    assert len(window._mask_cache) == initial_mask_count
    window.close()
    window.deleteLater()
    app.processEvents()


def test_hybrid_view_only_replaces_idle_sprite_after_ready() -> None:
    app, window = _create_window()
    fake_view = QLabel(window)
    window._live2d_view = fake_view
    window._live2d_ready = True

    window.set_state(PetState.IDLE)
    assert fake_view.isVisible()
    assert not window.label.isVisible()

    window.set_state(PetState.WALK)
    assert not fake_view.isVisible()
    assert window.label.isVisible()

    window.close()
    window.deleteLater()
    app.processEvents()


def test_explicit_live2d_state_map_keeps_model_visible_across_states() -> None:
    """显式模型状态映射应避免 Live2D 与低清精灵来回切换。"""

    app, window = _create_window()

    class _FakeLive2DView(QLabel):
        def __init__(self, parent) -> None:
            super().__init__(parent)
            self.presentations: list[tuple[dict[str, object], int]] = []

        def apply_presentation(
            self,
            presentation: dict[str, object],
            *,
            frame_index: int = 0,
        ) -> None:
            self.presentations.append((dict(presentation), frame_index))

    fake_view = _FakeLive2DView(window)
    window._live2d_view = fake_view
    window._live2d_ready = True
    window._hybrid_runtime_config = {
        "live2d_state_map": {
            "idle": {},
            "walk": {},
            "happy": {"expression": "star_eyes"},
        }
    }

    window.set_state(PetState.WALK)
    assert fake_view.isVisible()
    assert not window.label.isVisible()
    assert fake_view.presentations[-1][0] == {}
    assert window.mask() == QRegion(window.rect())

    window.set_state(PetState.HAPPY)
    assert fake_view.isVisible()
    assert fake_view.presentations[-1][0] == {"expression": "star_eyes"}

    window.close()
    window.deleteLater()
    app.processEvents()


def test_hybrid_view_loads_visibly_behind_sprite_before_ready() -> None:
    """idle 加载期不能隐藏 WebEngine，否则 ready 握手可能永久停住。"""

    app, window = _create_window()
    fake_view = QLabel(window)
    window._live2d_view = fake_view
    window._live2d_ready = False

    window.set_state(PetState.IDLE)

    assert fake_view.isVisible()
    assert window.label.isVisible()
    window.set_state(PetState.WALK)
    assert not fake_view.isVisible()
    assert window.label.isVisible()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_hybrid_idle_uses_full_window_mask_instead_of_sprite_bounds() -> None:
    """Live2D 不能被画布留白不同的待机 PNG 包围框横向裁掉帽子。"""

    app, window = _create_window()
    fake_view = QLabel(window)
    window._live2d_view = fake_view
    window._live2d_ready = True

    window.set_state(PetState.IDLE)
    live2d_mask = window.mask()
    assert live2d_mask == QRegion(window.rect())

    window.set_state(PetState.WALK)
    sprite_mask = window.mask()
    assert sprite_mask != QRegion(sprite_mask.boundingRect())

    window.close()
    window.deleteLater()
    app.processEvents()


def test_cursor_chase_reaches_mouse_and_switches_to_hug_pose() -> None:
    """追逐模式靠近鼠标后应停下并使用抱住姿态，不覆盖普通拖拽逻辑。"""

    app, window = _create_window()
    _enable_companion_behaviors(window)
    window.set_cursor_chase(True)
    cursor = window.frameGeometry().center()

    handled = window._cursor_chase_tick(time.monotonic(), 0.016, cursor)

    assert handled is True
    assert window._cursor_hugging is True
    assert window.state is PetState.DRAG
    window.close()
    window.deleteLater()
    app.processEvents()


def test_cursor_chase_keeps_facing_inside_horizontal_dead_zone() -> None:
    """目标基本位于正上/下方时，不应因像素误差左右反复镜像。"""

    app, window = _create_window()
    _enable_companion_behaviors(window)
    window.set_cursor_chase(True)
    window.direction = 1
    center = window.frameGeometry().center()

    assert window._cursor_chase_tick(
        10.0,
        0.0,
        center + QPoint(-8, 180),
    )
    assert window.direction == 1

    assert window._cursor_chase_tick(
        10.1,
        0.0,
        center + QPoint(-60, 180),
    )
    assert window.direction == -1
    window.close()
    window.deleteLater()
    app.processEvents()


def test_movement_region_toggle_persists_without_repositioning() -> None:
    """首次启用应生成当前位置附近的区域，但不能立刻拖回宠物。"""

    app, window = _create_window()
    original = QPoint(window.x(), window.y())

    window.set_movement_region_enabled(True)

    assert window.settings.movement_region_enabled is True
    assert window.settings.movement_region is not None
    assert QPoint(window.x(), window.y()) == original
    menu = window._build_context_menu()
    region_menu_action = next(
        action for action in menu.actions() if action.text() == "活动区域"
    )
    region_menu = region_menu_action.menu()
    assert region_menu is not None
    toggle = next(
        action for action in region_menu.actions()
        if action.text() == "启用区域限制"
    )
    assert toggle.isCheckable()
    assert toggle.isChecked()
    window.set_movement_region_enabled(False)
    assert window.settings.movement_region_enabled is False
    menu.deleteLater()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_custom_region_limits_chase_but_not_manual_screen_position() -> None:
    """追鼠标使用自定义矩形，手动拖动仍只受整块屏幕的防丢失边界保护。"""

    app, window = _create_window()
    screen = QApplication.primaryScreen()
    assert screen is not None
    screen_area = screen.availableGeometry()
    region = QRect(
        screen_area.left() + 20,
        screen_area.top() + 20,
        min(500, screen_area.width() - 40),
        min(400, screen_area.height() - 40),
    )
    window.set_movement_region(region)
    target = QPoint(screen_area.right(), region.center().y())

    movement_position = window._movement_constrained_position(target)
    manual_position = window._constrained_position(target)
    _minimum_x, maximum_x, minimum_y, maximum_y = (
        window._visible_position_limits(region)
    )

    assert movement_position.x() == maximum_x
    assert minimum_y <= movement_position.y() <= maximum_y
    assert manual_position.x() > movement_position.x()

    _enable_companion_behaviors(window)
    window.set_cursor_chase(True)
    window.move(region.left(), region.top())
    assert window._cursor_chase_tick(
        time.monotonic(),
        10.0,
        target,
    )
    assert window.x() <= maximum_x
    assert minimum_y <= window.y() <= maximum_y
    window.close()
    window.deleteLater()
    app.processEvents()


def test_manual_emotion_reuses_only_approved_base_states() -> None:
    """角色菜单可补充基础动作，但未知状态不能借此绕过动作白名单。"""

    app, window = _create_window()

    assert window.trigger_manual_emotion("curious", 1700) is True
    assert window.state is PetState.CURIOUS
    assert window.interaction_timer.interval() == 1700
    assert window.trigger_manual_emotion("shell", 1700) is False
    window.close()
    window.deleteLater()
    app.processEvents()


def test_interaction_menu_deduplicates_targets_and_triggers_declared_actions() -> None:
    """菜单按真实目标去重，且每个保留项都触发自己的序列或合法基础状态。"""

    app, window = _create_window()
    frame = window._pixmaps[PetState.HAPPY][0]
    window._extension_animations = {
        "hug": [frame, frame],
        "write": [frame, frame],
        "hidden_expression": [frame, frame],
    }
    window._extension_animation_config = {
        "hug": {
            "menu_label": "抱抱小熊",
            "base_state": "idle",
            "frame_duration_ms": 120,
        },
        "write": {
            "menu_label": "认真写字",
            "base_state": "idle",
            "frame_duration_ms": 120,
        },
        "hidden_expression": {
            "base_state": "idle",
            "frame_duration_ms": 120,
        },
    }
    window._companion_config = {
        "signature_action": {
            "enabled": True,
            "label": "抱抱小熊",
            "sequence": "hug",
            "state": "happy",
        },
        "manual_actions": [
            {
                "label": "重复抱熊",
                "sequence": "hug",
                "state": "happy",
            },
            {
                "label": "重复写字",
                "sequence": "write",
                "state": "idle",
            },
            {
                "label": "隐藏六连表情",
                "sequence": "hidden_expression",
                "state": "idle",
            },
            {
                "label": "好奇看看",
                "state": "curious",
                "duration_ms": 1700,
            },
            {
                "label": "无效空动作",
                "sequence": "missing",
                "state": "idle",
            },
        ],
    }

    menu = window._build_context_menu()
    interaction_menu = next(
        action.menu() for action in menu.actions() if action.text() == "互动动作"
    )
    assert interaction_menu is not None
    actions = interaction_menu.actions()
    assert [action.text() for action in actions] == [
        "打个招呼",
        "自拍一下",
        "抱抱小熊",
        "认真写字",
        "隐藏六连表情",
        "好奇看看",
    ]

    by_label = {action.text(): action for action in actions}
    by_label["抱抱小熊"].trigger()
    assert window._extension_active_name == "hug"
    window._clear_extension_animation()
    by_label["认真写字"].trigger()
    assert window._extension_active_name == "write"
    window._clear_extension_animation()
    by_label["隐藏六连表情"].trigger()
    assert window._extension_active_name == "hidden_expression"
    window._clear_extension_animation()
    by_label["好奇看看"].trigger()
    assert window.state is PetState.CURIOUS
    assert window.interaction_timer.interval() == 1700

    menu.deleteLater()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_keyboard_burst_triggers_one_privacy_safe_companion_reaction() -> None:
    """短时按键数量达到阈值时触发一次挥手，冷却内不重复触发。"""

    app, window = _create_window()
    _enable_companion_behaviors(window)

    assert window._handle_keyboard_activity(2, now=10.0) is False
    assert window._handle_keyboard_activity(1, now=10.2) is True
    assert window.state is PetState.WAVE
    assert window._handle_keyboard_activity(3, now=10.4) is False
    window.close()
    window.deleteLater()
    app.processEvents()


def test_focus_session_sits_quietly_then_celebrates() -> None:
    """专注开始时进入坐姿，完成后播放配置的开心庆祝。"""

    app, window = _create_window()
    _enable_companion_behaviors(window)

    assert window.start_focus_session(duration_ms=5000) is True
    assert window._focus_active is True
    assert window.state is PetState.SIT
    window._complete_focus_session()
    assert window._focus_active is False
    assert window.state is PetState.HAPPY
    window.close()
    window.deleteLater()
    app.processEvents()


def test_signature_action_uses_manifest_declared_state() -> None:
    """招牌动作由清单声明，运行时不需要角色专用 Python 分支。"""

    app, window = _create_window()
    _enable_companion_behaviors(window)

    assert window.trigger_signature_action() is True
    assert window.state is PetState.WAVE
    window.close()
    window.deleteLater()
    app.processEvents()


def test_extension_sequences_drive_signature_focus_and_single_play_actions() -> None:
    """扩展帧应可复用于招牌、专注循环和菜单单次动作，且不覆盖基础帧。"""

    app, window = _create_window()
    _enable_companion_behaviors(window)
    wave_frames = list(window._pixmaps[PetState.IDLE][:4])
    focus_frames = list(window._pixmaps[PetState.IDLE][:3])
    window._extension_animations = {
        "signature_wave": wave_frames,
        "focus_work": focus_frames,
        "jump": wave_frames[:2],
    }
    window._extension_animation_config = {
        "signature_wave": {
            "frame_duration_ms": 180,
            "loop": False,
            "base_state": "wave",
        },
        "focus_work": {
            "frame_duration_ms": 260,
            "loop": False,
            "base_state": "sit",
        },
        "jump": {
            "frame_duration_ms": 120,
            "loop": False,
            "base_state": "idle",
        },
    }
    window._companion_config["signature_action"]["sequence"] = "signature_wave"
    window._companion_config["focus_session"]["loop_sequence"] = "focus_work"

    assert window.trigger_signature_action() is True
    assert window.state is PetState.WAVE
    assert window._extension_active_name == "signature_wave"
    window._extension_tick()
    assert window._extension_frame_index == 1

    assert window.start_focus_session(duration_ms=5000) is True
    assert window.state is PetState.SIT
    assert window._extension_active_name == "focus_work"
    assert window._extension_animation_config["focus_work"]["loop"] is False
    window.cancel_focus_session()
    assert window._extension_active_name is None

    assert window.trigger_extension_action("jump") is True
    assert window._extension_active_name == "jump"
    window._extension_tick()
    window._extension_tick()
    assert window._extension_active_name is None
    window.close()
    window.deleteLater()
    app.processEvents()


def test_autoplay_signature_actions_wait_for_idle_and_avoid_recent_repeats() -> None:
    """自动招牌动作只能在空闲时启动，并从防重复窗口之外选择。"""

    app, window = _create_window()
    frame = window._pixmaps[PetState.IDLE][0]
    window._extension_animations = {
        "slow_blink": [frame],
        "head_tilt": [frame],
        "knead": [frame],
    }
    window._extension_animation_config = {
        name: {
            "frame_duration_ms": 120,
            "loop": False,
            "base_state": "idle",
        }
        for name in window._extension_animations
    }
    window._companion_config["auto_signature_actions"] = {
        "enabled": True,
        "minimum_idle_ms": 1000,
        "chance": 1.0,
        "no_repeat_count": 2,
        "retry_delay_ms": [250, 250],
        "interval_ms": [250, 250],
        "actions": [
            {"sequence": "slow_blink", "weight": 1.0},
            {"sequence": "head_tilt", "weight": 1.0},
            {"sequence": "knead", "weight": 1.0, "cycles": 3},
        ],
    }
    window.autoplay_timer.stop()
    window._last_user_interaction = time.monotonic() - 2
    window._autoplay_recent.extend(("slow_blink", "head_tilt"))
    window.set_state(PetState.IDLE)

    assert window._autoplay_action_tick() is True
    assert window._extension_active_name == "knead"
    assert window._autoplay_cycles_remaining == 3
    assert list(window._autoplay_recent) == ["head_tilt", "knead"]

    window._clear_extension_animation()
    window._autoplay_active_sequence = None
    window._autoplay_cycles_remaining = 0
    window.set_state(PetState.WALK)
    window._last_user_interaction = time.monotonic() - 2
    assert window._autoplay_action_tick() is False
    assert window.autoplay_timer.isActive() is True
    window.close()
    window.deleteLater()
    app.processEvents()


def test_auto_expression_rotation_uses_one_frame_and_avoids_recent_repeats() -> None:
    """自动微表情应单帧防重复，并在真实鼠标按下时立即退出。"""

    app, window = _create_window()
    window.autoplay_timer.stop()
    frame = window._pixmaps[PetState.IDLE][0]
    window._extension_animations["six_faces"] = [frame] * 6
    window._extension_animation_config["six_faces"] = {
        "frame_duration_ms": 360,
        "loop": False,
        "base_state": "idle",
        "hold_last_ms": 800,
    }
    window._companion_config["auto_expression_rotation"] = {
        "enabled": True,
        "sequence": "six_faces",
        "minimum_idle_ms": 1000,
        "chance": 1.0,
        "no_repeat_count": 5,
        "duration_ms": [900, 900],
        "retry_delay_ms": [250, 250],
        "interval_ms": [250, 250],
    }
    window.auto_expression_timer.stop()
    window._auto_expression_recent.extend((0, 1, 2, 3, 4))
    window._last_user_interaction = time.monotonic() - 2
    last_interaction = window._last_user_interaction
    window.set_state(PetState.IDLE)

    assert window._auto_expression_rotation_tick() is True
    assert window._extension_active_name == "six_faces"
    assert window._extension_frame_index == 5
    assert window._extension_single_frame is True
    assert window.extension_timer.interval() == 900
    assert window.auto_expression_timer.isActive() is True
    assert window._last_user_interaction == last_interaction

    window._extension_tick()
    app.processEvents()
    assert window._extension_active_name is None
    assert window._extension_single_frame is False

    window._press_pending = True
    window.set_state(PetState.IDLE)
    window._last_user_interaction = time.monotonic() - 2
    assert window._auto_expression_rotation_tick() is False
    assert window.auto_expression_timer.isActive() is True
    window._press_pending = False

    assert window.play_extension_animation("six_faces") is True
    assert window._extension_single_frame is False
    assert window._extension_frame_index == 0
    window._extension_tick()
    assert window._extension_frame_index == 1
    window._clear_extension_animation()

    window.set_state(PetState.WALK)
    window._last_user_interaction = time.monotonic() - 2
    assert window._auto_expression_rotation_tick() is False
    assert window.auto_expression_timer.isActive() is True

    window.set_state(PetState.IDLE)
    window._last_user_interaction = time.monotonic() - 2
    assert window._auto_expression_rotation_tick() is True
    assert window._extension_single_frame is True
    QTest.mousePress(
        window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(window.width() // 2, window.height() // 2),
    )
    app.processEvents()
    assert window._press_pending is True
    assert window._extension_active_name is None
    assert window._extension_single_frame is False
    assert window.extension_timer.isActive() is False
    # 即便模拟一次过期 timeout，已清空的扩展也只能停表，不能在按住时
    # 进入 _finish_interaction() 并重新安排自主行为。
    window._extension_tick()
    app.processEvents()
    assert window.state_timer.isActive() is False
    assert window._press_pending is True
    window.close()
    window.deleteLater()
    app.processEvents()


def test_care_play_reuses_an_approved_versioned_jump_extension() -> None:
    """养成玩耍应兼容已审批的版本化轻盈跳跃动作名。"""

    app, window = _create_window()
    jump_frames = list(window._pixmaps[PetState.IDLE][:2])
    window._care_config = normalize_care_config({"enabled": True})
    window._extension_animations = {"happy_jump_v2": jump_frames}
    window._extension_animation_config = {
        "happy_jump_v2": {
            "frame_duration_ms": 110,
            "loop": False,
            "base_state": "idle",
        }
    }

    assert window.care_play() is True
    assert window._extension_active_name == "happy_jump_v2"
    window.close()
    window.deleteLater()
    app.processEvents()


def test_directional_gaze_only_replaces_idle_and_yields_to_extension_frames() -> None:
    """八向视线只服务待机，走路、表情和扩展动作必须保持自己的素材。"""

    app, window = _create_window()
    directional = window._pixmaps[PetState.IDLE][0]
    extension = window._pixmaps[PetState.HAPPY][0]
    window._directional_pixmaps = {"center": directional, "east": directional}
    window._look_direction = "east"
    window._extension_animations = {"jump": [extension]}
    window._extension_animation_config = {
        "jump": {
            "frame_duration_ms": 120,
            "loop": False,
            "base_state": "idle",
        }
    }

    window.set_state(PetState.WAVE)
    _state, wave_source = window._current_source()
    assert wave_source.cacheKey() == window._pixmaps[PetState.WAVE][0].cacheKey()

    assert window.play_extension_animation("jump") is True
    _state, extension_source = window._current_source()
    assert extension_source.cacheKey() == extension.cacheKey()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_local_dialogue_bubble_shows_action_line_and_stays_on_screen() -> None:
    """手动动作应显示纯本地文字气泡，并限制在当前屏幕可用区域。"""

    app, window = _create_window()
    window._dialogue_config = normalize_dialogue_config(
        {
            "enabled": True,
            "cooldown_ms": 0,
            "lines": {"wave": ["你好，我是 OnePic Demo。"]},
        }
    )
    window._dialogue_pet_id = "gugugaga2"
    window._reset_dialogue_selector()

    assert window._maybe_show_dialogue(
        "wave",
        source="manual",
        force=True,
    )
    app.processEvents()

    assert window.dialogue_bubble.isVisible()
    assert window.dialogue_bubble.text() == "你好，我是 OnePic Demo。"
    area = window._screen_geometry()
    assert area is not None
    assert area.contains(window.dialogue_bubble.frameGeometry())
    window.close()
    window.deleteLater()
    app.processEvents()


def test_dialogue_menu_edits_and_resets_current_pet_overrides(
    monkeypatch,
) -> None:
    """右键编辑应按宠物保存每行一句的覆盖，并可恢复默认。"""

    app, window = _create_window()
    window._dialogue_config = normalize_dialogue_config(
        {
            "enabled": True,
            "cooldown_ms": 0,
            "action_labels": {"wave": "挥手"},
            "lines": {"wave": ["默认问候"]},
        }
    )
    window._dialogue_pet_id = "gugugaga2"
    window._reset_dialogue_selector()
    monkeypatch.setattr(
        "onepic_desktop_pet.window.QInputDialog.getMultiLineText",
        lambda *_args, **_kwargs: ("自定义一\n自定义二\n自定义一", True),
    )

    assert window.edit_dialogue_lines("wave")
    assert window.settings.dialogue_overrides == {
        "gugugaga2": {"wave": ["自定义一", "自定义二"]}
    }
    assert window._dialogue_selector.lines_for("wave") == [
        "自定义一",
        "自定义二",
    ]
    menu = window._build_context_menu()
    dialogue_action = next(
        action for action in menu.actions() if action.text() == "聊天词"
    )
    dialogue_menu = dialogue_action.menu()
    assert dialogue_menu is not None
    assert [action.text() for action in dialogue_menu.actions()] == [
        "随机说一句",
        "编辑聊天词",
        "",
        "恢复默认聊天词",
    ]

    window.reset_dialogue_lines()
    assert "gugugaga2" not in window.settings.dialogue_overrides
    assert window._dialogue_selector.lines_for("wave") == ["默认问候"]
    menu.deleteLater()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_variant_switches_frames_and_exposes_checked_menu_choice() -> None:
    """正面与侧面走路应可选、可记忆，并始终保持八相位序列。"""

    app, window = _create_window()
    front_frames = window._pixmaps[PetState.WALK]
    side_seed = window._pixmaps[PetState.HAPPY][0]
    side_frames = [side_seed] * 8
    assert len(front_frames) == 8
    assert len(side_frames) == 8
    window._walk_variant_pixmaps = {
        "front": front_frames,
        "side_walk_v1": side_frames,
    }
    window._walk_variant_labels = {
        "front": "正面走路",
        "side_walk_v1": "侧面走路",
    }
    window._walk_variant_mirror = {
        "front": True,
        "side_walk_v1": True,
    }
    window.direction = 1

    window.set_walk_variant("side_walk_v1")
    window.set_state(PetState.WALK)
    _state, source = window._current_source()
    assert window.settings.walk_variant == "side_walk_v1"
    assert source.cacheKey() == side_frames[0].cacheKey()

    menu = window._build_context_menu()
    walk_menu = next(
        action.menu()
        for action in menu.actions()
        if action.text() == "走路样式"
    )
    assert walk_menu is not None
    actions = {action.text(): action for action in walk_menu.actions()}
    assert set(actions) == {"正面走路", "侧面走路"}
    assert actions["侧面走路"].isChecked()
    assert not actions["正面走路"].isChecked()

    window.set_walk_variant("front")
    _state, source = window._current_source()
    assert source.cacheKey() == front_frames[0].cacheKey()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_hybrid_idle_stays_long_enough_for_complete_slow_blink() -> None:
    """主状态机不能在长周期自动眨眼开始前离开待机。"""

    app, window = _create_window()
    window._hybrid_runtime_config = {
        "enabled": True,
        "idle_backend": "live2d",
        "idle_animation": {
            "blink": True,
            "blink_interval_ms": [15000, 18000],
            "blink_duration_ms": [700, 1000, 1200],
        },
    }

    window._schedule(StateDecision(PetState.IDLE, 3000))

    assert window.state is PetState.IDLE
    assert window._hybrid_idle_minimum_duration_ms() == 21400
    assert window.state_timer.interval() == 21400
    assert window.state_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_hybrid_idle_extension_does_not_change_other_states() -> None:
    """Live2D 眨眼驻留契约不能延长走路等精灵状态。"""

    app, window = _create_window()
    window._hybrid_runtime_config = {
        "enabled": True,
        "idle_backend": "live2d",
        "idle_animation": {
            "blink": True,
            "blink_interval_ms": [15000, 18000],
            "blink_duration_ms": [700, 1000, 1200],
        },
    }

    window._schedule(StateDecision(PetState.WALK, 3000))

    assert window.state is PetState.WALK
    assert 2500 <= window.state_timer.remainingTime() <= 3000
    window.close()
    window.deleteLater()
    app.processEvents()


def test_live2d_ready_restarts_full_idle_blink_window() -> None:
    """模型加载时间不能吃掉长周期眨眼所需的 idle 驻留。"""

    app, window = _create_window()
    window._hybrid_runtime_config = {
        "enabled": True,
        "idle_backend": "live2d",
        "idle_animation": {
            "blink": True,
            "blink_interval_ms": [15000, 18000],
            "blink_duration_ms": [700, 1000, 1200],
        },
    }
    window.set_state(PetState.IDLE)
    window.state_timer.start(1000)

    window._live2d_ready_changed(True)

    assert window._live2d_ready
    assert window.state_timer.interval() == 21400
    assert window.state_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_sit_animation_holds_then_reverses_to_standing_frame() -> None:
    app, window = _create_window()
    window.set_state(PetState.SIT)

    for _ in range(len(window._pixmaps[PetState.SIT])):
        window._animation_tick()

    assert window._frame_index == len(window._pixmaps[PetState.SIT]) - 1
    assert not window.animation_timer.isActive()

    window._reverse_transition_to_idle()
    for _ in range(len(window._pixmaps[PetState.SIT]) - 1):
        window._animation_tick()

    assert window._frame_index == 0
    assert not window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_front_walk_mirrors_only_in_its_configured_direction() -> None:
    app, window = _create_window()
    front_frame = window._pixmaps[PetState.WALK][0]
    window._front_walk_mirror_direction = "right"
    window.direction = -1
    window.set_state(PetState.WALK)

    _state, source = window._current_source()

    assert source.cacheKey() == front_frame.cacheKey()
    window.direction = 1
    _state, mirrored_source = window._current_source()
    assert mirrored_source.cacheKey() != front_frame.cacheKey()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_pauses_briefly_when_turning_at_screen_edge() -> None:
    app, window = _create_window()
    window.set_state(PetState.WALK)
    window.direction = -1
    window._frame_index = 3
    area = QRect(0, 0, 1000, 1000)
    minimum_x, _maximum_x, _minimum_y, _maximum_y = (
        window._visible_position_limits(area)
    )
    window.move(minimum_x, 0)
    window._screen_geometry = lambda: area

    window._movement_tick()

    assert window.direction == 1
    assert window._turn_paused
    assert window.turn_timer.isActive()
    assert not window.animation_timer.isActive()

    window._finish_turn()

    assert not window._turn_paused
    assert window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_visible_bounds_can_reach_every_screen_edge() -> None:
    """透明画布留白不得阻止角色可见轮廓贴到屏幕四角。"""

    app, window = _create_window()
    window.setMask(QRegion(QRect(80, 60, 120, 180)))
    area = QRect(0, 0, 1000, 800)

    minimum_x, maximum_x, minimum_y, maximum_y = (
        window._visible_position_limits(area)
    )

    assert (minimum_x, maximum_x, minimum_y, maximum_y) == (
        -80,
        800,
        -60,
        560,
    )
    visible = window.mask().boundingRect()
    assert maximum_x + visible.right() == area.right()
    assert maximum_y + visible.bottom() == area.bottom()
    assert minimum_x + visible.left() == area.left()
    assert minimum_y + visible.top() == area.top()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_vertical_offset_follows_footfall_frames() -> None:
    """行走起伏必须跟随连续帧，而不是由独立的慢速浮动计时器驱动。"""

    app, window = _create_window()
    window.set_state(PetState.WALK)
    offsets = [window.label.y()]

    for _ in range(3):
        window._animation_tick()
        offsets.append(window.label.y())

    assert offsets == [2, 3, 2, 1]
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_uses_subpixel_phase_synced_speed(monkeypatch) -> None:
    """水平移动应亚像素累计，落脚阶段减速而不冻结，随后平滑加速。"""

    app, window = _create_window()
    window.set_state(PetState.WALK)
    window.direction = 1
    window.move(100, 0)
    window._movement_x = 100.0
    window._last_movement_at = 10.0
    window._screen_geometry = lambda: QRect(0, 0, 1000, 1000)
    current_time = [10.016]
    monkeypatch.setattr(
        "onepic_desktop_pet.window.time.monotonic",
        lambda: current_time[0],
    )

    window._frame_index = 0
    window._movement_tick()
    assert round(window._movement_x, 2) == 100.78
    assert window.x() == 101

    current_time[0] = 10.032
    window._frame_index = 3
    window._movement_tick()
    assert window._movement_speed_pixels_per_second() == 62.5
    assert round(window._movement_x, 2) == 101.94
    assert window.x() == 102
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_motion_curve_avoids_freeze_and_balances_both_steps() -> None:
    """移动曲线不应停顿后猛跳，且左右两个半步必须使用相同节奏。"""

    app, window = _create_window()

    assert min(window._walk_motion_factors) > 0.0
    assert max(window._walk_motion_factors) / min(window._walk_motion_factors) < 4
    assert window._walk_motion_factors[:4] == window._walk_motion_factors[4:]
    assert sum(window._walk_motion_factors) / 8 == 1.0
    cyclic_deltas = [
        abs(
            window._walk_motion_factors[index]
            - window._walk_motion_factors[(index + 1) % 8]
        )
        for index in range(8)
    ]
    assert max(cyclic_deltas) <= 0.4

    window.close()
    window.deleteLater()
    app.processEvents()


def test_side_walk_uses_its_approved_preview_timing() -> None:
    """侧走样式应使用扩展包帧时长，避免被正面快节奏强制加速。"""

    app, window = _create_window()
    window._extension_animation_config["side_walk_v6"] = {
        "frame_duration_ms": 115,
    }

    window._active_walk_variant = "side_walk_v6"
    assert window._frame_interval(PetState.WALK, 0) == 115
    window._active_walk_variant = "front"
    assert window._frame_interval(PetState.WALK, 0) == 90
    window.close()
    window.deleteLater()
    app.processEvents()


def test_drag_state_uses_dedicated_suspended_animation() -> None:
    """拖拽状态应加载三帧悬空素材，而不是回退到待机站立。"""

    app, window = _create_window()
    window.set_state(PetState.DRAG)

    display_state, _pixmap = window._current_source()
    assert display_state is PetState.DRAG
    assert len(window._pixmaps[PetState.DRAG]) == 3
    assert window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_interaction_states_have_reusable_emotion_symbols() -> None:
    """互动表情应使用独立符号层，换角色素材后仍然能够显示。"""

    expected = {
        PetState.HAPPY: "sparkle",
        PetState.SHY: "heart",
        PetState.SURPRISED: "exclamation",
        PetState.ANNOYED: "anger",
        PetState.SLEEPY: "sleep",
        PetState.CURIOUS: "question",
        PetState.SELFIE: "flash",
        PetState.DRAG: "sweat",
    }
    assert {state: emotion_effect_name(state) for state in expected} == expected
    assert emotion_effect_name(PetState.IDLE) is None


def test_mouse_look_vector_maps_to_center_and_eight_directions() -> None:
    """原视频桌宠的视线方向应覆盖中心与八向，并保持屏幕坐标语义。"""

    classify = PetWindow._direction_from_vector
    assert classify(5, 5, 20) == "center"
    assert classify(100, 0) == "east"
    assert classify(100, -100) == "north_east"
    assert classify(0, -100) == "north"
    assert classify(-100, -100) == "north_west"
    assert classify(-100, 0) == "west"
    assert classify(-100, 100) == "south_west"
    assert classify(0, 100) == "south"
    assert classify(100, 100) == "south_east"


def test_continuous_mouse_look_ring_starts_up_and_moves_clockwise() -> None:
    """32 向连续帧应让鼠标绕行与原视频的顺时针转头顺序一致。"""

    classify = PetWindow._ring_index_from_vector
    assert classify(0, -100, 32) == 0
    assert classify(100, 0, 32) == 8
    assert classify(0, 100, 32) == 16
    assert classify(-100, 0, 32) == 24
    assert classify(-10, -100, 32) == 31


def test_directional_manifest_loads_as_stationary_mouse_follow_pet(
    monkeypatch,
) -> None:
    """可选清单应加载九向帧并禁止桌宠自行横向行走。"""

    manifest = Path("tests/fixtures/directional_manifest.json").resolve()
    directions = (
        "center",
        "north",
        "north_east",
        "east",
        "south_east",
        "south",
        "south_west",
        "west",
        "north_west",
    )
    monkeypatch.delenv("ONEPIC_USE_DEMO_ASSETS")
    monkeypatch.setenv("ONEPIC_PET_MANIFEST", str(manifest))

    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings())

    assert window._stationary
    assert set(window._directional_pixmaps) == set(directions)
    assert window.look_timer.isActive()
    start = window.pos()
    window.set_state(PetState.WALK)
    window._movement_tick()
    assert window.pos() == start
    window.close()
    window.deleteLater()
    app.processEvents()


def test_emotion_symbol_timer_follows_current_state() -> None:
    """进入表情状态时符号应动画，恢复待机后必须停止计时器。"""

    app, window = _create_window()
    window.set_state(PetState.SURPRISED)
    assert window.effect_timer.isActive()
    assert not window.label.pixmap().isNull()

    window.set_state(PetState.IDLE)
    assert not window.effect_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_inactivity_progresses_from_sit_to_sleep() -> None:
    """超过睡眠阈值后仍应先完整坐下，再播放坐姿入睡序列。"""

    settings = PetSettings(inactive_sit_ms=10000, inactive_sleep_ms=20000)
    app = QApplication.instance() or QApplication([])
    window = PetWindow(settings)
    window._last_user_interaction = time.monotonic() - 21
    window.set_state(PetState.IDLE)

    window._state_timeout()
    assert window.state is PetState.SIT
    assert window._sleep_after_sit

    window._state_timeout()
    assert window.state is PetState.SLEEP
    assert not window._sleep_after_sit
    window.close()
    window.deleteLater()
    app.processEvents()


def test_pause_disables_running_but_keeps_ambient_state_timer() -> None:
    """暂停跑动时应进入生活状态并继续计时，而不是冻结在站立帧。"""

    app, window = _create_window()
    window.set_state(PetState.WALK)
    window.behavior.next_autonomous_state = (
        lambda _current, allow_walk: StateDecision(PetState.SIT, 2000)
    )

    window.set_paused(True)

    assert window.paused
    assert window.state is PetState.SIT
    assert window.state_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_display_size_preset_updates_geometry_and_settings() -> None:
    """右键尺寸预设应立即改变窗口和标签尺寸，并写回设置对象。"""

    app, window = _create_window()
    window.set_display_height(450)

    assert window.settings.display_height == 450
    assert window.height() == 464
    assert window.label.height() == 458
    assert not window.mask().isEmpty()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_size_menu_offers_real_person_and_custom_sizes() -> None:
    """真人角色应有更大预设，并保留 120–600 像素的自定义入口。"""

    app, window = _create_window()
    assert ("真人大（450）", 450) in DISPLAY_SIZE_PRESETS
    assert ("最大（560）", 560) in DISPLAY_SIZE_PRESETS

    menu = window._build_context_menu()
    size_action = next(action for action in menu.actions() if action.text() == "宠物大小")
    size_menu = size_action.menu()
    assert size_menu is not None
    labels = [action.text() for action in size_menu.actions()]
    assert "真人大（450）" in labels
    assert "最大（560）" in labels
    assert any(label.startswith("自定义大小") for label in labels)
    wheel_action = next(
        action for action in size_menu.actions()
        if action.text() == "滚轮调节大小"
    )
    assert wheel_action.menu() is not None
    assert [action.text() for action in wheel_action.menu().actions()] == [
        label for label, _mode in WHEEL_RESIZE_MODES
    ]
    menu.deleteLater()


def _send_wheel(
    window: PetWindow,
    delta_y: int,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    """向离屏窗口发送一格标准滚轮事件。"""

    center = QPointF(window.rect().center())
    event = QWheelEvent(
        center,
        QPointF(window.mapToGlobal(window.rect().center())),
        QPoint(),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(window, event)


def test_ctrl_wheel_resize_requires_modifier_and_preserves_bottom_anchor() -> None:
    """推荐模式只响应 Ctrl 滚轮，并保持缩放前后的脚底位置。"""

    app, window = _create_window()
    window.move(200, 200)
    app.processEvents()
    old_height = window.settings.display_height
    old_bottom = window.y() + window.height()

    _send_wheel(window, 120)
    assert window.settings.display_height == old_height

    _send_wheel(window, 120, Qt.KeyboardModifier.ControlModifier)
    assert window.settings.display_height == old_height + 10
    assert window.y() + window.height() == old_bottom
    window.close()
    window.deleteLater()
    app.processEvents()


def test_direct_wheel_resize_and_disabled_mode() -> None:
    """直接模式无需组合键，关闭模式则完全不调整尺寸。"""

    app, window = _create_window()
    window.set_wheel_resize_mode("direct")
    _send_wheel(window, -120)
    assert window.settings.display_height == 210

    window.set_wheel_resize_mode("disabled")
    _send_wheel(window, 120)
    assert window.settings.display_height == 210
    window.close()
    window.deleteLater()
    app.processEvents()


def test_switchable_pet_menu_changes_manifest_and_remembers_choice(
    tmp_path,
    monkeypatch,
) -> None:
    """右键形象菜单应热切换两套素材并写回设置对象。"""

    source_manifest = Path("tests/fixtures/directional_manifest.json").resolve()
    root = tmp_path / "pet_profiles"
    desk = root / "desk"
    boots = root / "boots"
    desk.mkdir(parents=True)
    boots.mkdir(parents=True)
    source_data = json.loads(source_manifest.read_text(encoding="utf-8"))
    for target in (desk, boots):
        (target / "frame.png").write_bytes(
            (
                source_manifest.parent
                / source_data["animations"]["idle"][0]
            ).resolve().read_bytes()
        )
    for paths in source_data["animations"].values():
        paths[:] = ["frame.png"]
    source_data["directional_mouse_follow"]["frames"] = {
        name: "frame.png"
        for name in source_data["directional_mouse_follow"]["frames"]
    }
    for target in (desk, boots):
        (target / "manifest.json").write_text(
            json.dumps(source_data),
            encoding="utf-8",
        )
    boots_data = dict(source_data)
    boots_data["stationary"] = False
    boots_data.pop("directional_mouse_follow")
    (boots / "manifest.json").write_text(
        json.dumps(boots_data),
        encoding="utf-8",
    )
    catalog = tmp_path / "pet_catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "default_pet_id": "desk-follow",
                "profiles": [
                    {
                        "id": "desk-follow",
                        "label": "嘟嘴半身（鼠标跟随）",
                        "manifest": "pet_profiles/desk/manifest.json",
                    },
                    {
                        "id": "black-boots",
                        "label": "黑衣长靴女孩（全身）",
                        "manifest": "pet_profiles/boots/manifest.json",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("ONEPIC_PET_MANIFEST", raising=False)
    monkeypatch.delenv("ONEPIC_USE_DEMO_ASSETS", raising=False)
    monkeypatch.setenv("ONEPIC_PET_CATALOG", str(catalog))
    monkeypatch.setattr(
        "onepic_desktop_pet.window.character_is_approved",
        lambda _workflow: True,
    )
    monkeypatch.setattr(
        "onepic_desktop_pet.window.load_workflow",
        lambda _path=None: object(),
    )

    app = QApplication.instance() or QApplication([])
    settings = PetSettings(display_height=360)
    window = PetWindow(settings)
    assert window._stationary

    menu = window._build_context_menu()
    profile_action = next(
        action for action in menu.actions() if action.text() == "切换形象"
    )
    labels = [action.text() for action in profile_action.menu().actions()]
    assert labels == ["嘟嘴半身（鼠标跟随）", "黑衣长靴女孩（全身）"]

    change_notifications: list[str] = []

    class _CompanionObserver:
        def on_pet_changed(self) -> None:
            change_notifications.append(window.companion_pet_id())

    window.attach_companion_controller(_CompanionObserver())

    window.switch_pet_profile("black-boots")

    assert settings.pet_id == "black-boots"
    assert window._active_pet_id == "black-boots"
    assert not window._stationary
    assert not window.look_timer.isActive()
    assert settings.display_height == 360
    assert change_notifications == ["black-boots"]
    menu.deleteLater()
    window.close()
    window.deleteLater()
    app.processEvents()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_interaction_zones_map_head_face_body_and_camera() -> None:
    """窗口相对位置应稳定映射为四种点击区域。"""

    app, window = _create_window()
    center_x = window.label.x() + window.label.width() // 2
    assert window._interaction_zone(QPoint(center_x, 20)) == "head"
    assert (
        window._interaction_zone(
            QPoint(center_x, round(window.label.height() * 0.34))
        )
        == "face"
    )
    assert (
        window._interaction_zone(
            QPoint(
                window.label.x() + round(window.label.width() * 0.2),
                round(window.label.height() * 0.62),
            )
        )
        == "camera"
    )
    assert (
        window._interaction_zone(
            QPoint(center_x, round(window.label.height() * 0.7))
        )
        == "body"
    )
    window.close()
    window.deleteLater()
    app.processEvents()


def test_head_click_increases_affinity_and_repeated_body_poke_annoys() -> None:
    """摸头应提升亲密度，短时间连续戳身体应切换到轻微生气表情。"""

    app, window = _create_window()
    initial_affinity = window.mood.affinity
    head = QPoint(window.width() // 2, 20)
    body = QPoint(window.width() // 2, round(window.label.height() * 0.7))

    window._handle_click(head)
    assert window.mood.affinity == initial_affinity + 5
    assert window.state is PetState.SURPRISED
    assert window._touch_active
    window._advance_touch_sequence()
    assert window.state in (PetState.SHY, PetState.HAPPY)
    window._cancel_touch_sequence()

    for _ in range(3):
        window._handle_click(body)
    assert window.state is PetState.ANNOYED
    assert window.mood.affinity < initial_affinity + 5
    window.close()
    window.deleteLater()
    app.processEvents()


def test_state_fallback_suppresses_disabled_annoyed_reaction() -> None:
    """禁用生气表情的宠物连续被戳时应回退惊讶，不显示生气状态。"""

    app, window = _create_window()
    window._state_fallbacks = {PetState.ANNOYED: PetState.SURPRISED}
    body = QPoint(window.width() // 2, round(window.label.height() * 0.7))

    for _ in range(3):
        window._handle_click(body)

    assert window.state is PetState.SURPRISED
    assert emotion_effect_name(window.state) == "exclamation"
    window.close()
    window.deleteLater()
    app.processEvents()


def test_touch_sequence_throttles_repeat_and_returns_smoothly_to_idle() -> None:
    """摸头期间连点应忽略，并按惊讶、缓和、过渡、待机顺序播放。"""

    app, window = _create_window()
    head = window.mask().boundingRect().topLeft() + QPoint(
        window.mask().boundingRect().width() // 2,
        4,
    )
    initial_affinity = window.mood.affinity

    window._handle_click(head)
    assert window.state is PetState.SURPRISED
    assert window._touch_active
    window._handle_click(head)
    assert window.mood.affinity == initial_affinity + 5

    window._advance_touch_sequence()
    reaction = window.state
    assert reaction in (PetState.SHY, PetState.HAPPY)
    window._advance_touch_sequence()
    assert window._touch_override_pixmap is not None
    window._advance_touch_sequence()
    assert window.state is PetState.IDLE
    window._advance_touch_sequence()
    assert not window._touch_active
    assert window.state is PetState.IDLE
    window.close()
    window.deleteLater()
    app.processEvents()


def test_touch_default_branch_does_not_repeat_consecutively() -> None:
    """默认 weighted_no_repeat 策略的相邻两次反馈不能相同。"""

    app, window = _create_window()
    first = window._choose_touch_branch()
    second = window._choose_touch_branch()
    assert first in (PetState.SHY, PetState.HAPPY)
    assert second in (PetState.SHY, PetState.HAPPY)
    assert second is not first
    window.close()
    window.deleteLater()
    app.processEvents()


def test_head_hit_uses_alpha_bounds_and_rejects_body() -> None:
    """头部命中应随透明轮廓缩放，身体点不能误判为头。"""

    app, window = _create_window()
    bounds = window.mask().boundingRect()
    head = QPoint(bounds.center().x(), bounds.top() + 3)
    body = QPoint(bounds.center().x(), bounds.top() + round(bounds.height() * 0.65))

    assert window._is_head_hit(head)
    assert not window._is_head_hit(body)
    window.close()
    window.deleteLater()
    app.processEvents()


def test_selfie_completion_shows_photo_bubble() -> None:
    """没有用户原图时不得用生成动画末帧冒充自拍照片。"""

    app, window = _create_window()
    window._selfie_photo = type(window._selfie_photo)()
    window.set_state(PetState.SELFIE)
    window._finish_interaction()
    app.processEvents()

    assert not window.photo_bubble.isVisible()
    window.photo_bubble.hide()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_selfie_photo_uses_high_dpi_backing_pixels() -> None:
    """200% 缩放时横竖照片都应使用高分辨率像素并限制逻辑尺寸。"""

    app, window = _create_window()
    window._selfie_photo = window._pixmaps[PetState.SELFIE][-1]
    photo = window._scaled_selfie_photo(2.0)

    assert photo.devicePixelRatio() == 2.0
    assert max(photo.width(), photo.height()) >= 300
    assert round(photo.width() / photo.devicePixelRatio()) <= 150
    assert round(photo.height() / photo.devicePixelRatio()) <= 210
    window.close()
    window.deleteLater()
    app.processEvents()


def test_selfie_photo_is_positioned_near_visible_character() -> None:
    """照片应贴近人物不透明轮廓，而不是贴着含大块留白的窗口边缘。"""

    app, window = _create_window()
    window._selfie_photo = window._pixmaps[PetState.SELFIE][-1]
    window.move(500, 300)
    window._screen_geometry = lambda: QRect(0, 0, 1200, 900)
    window.set_state(PetState.SELFIE)
    window._show_photo_bubble()
    app.processEvents()

    character_left = window.x() + window.mask().boundingRect().left()
    visual_gap = character_left - (
        window.photo_bubble.x() + window.photo_bubble.width()
    )
    assert visual_gap == 8
    window.photo_bubble.hide()
    window.close()
    window.deleteLater()
    app.processEvents()
