"""验证 Live2D 关键姿态捕获工具的猪鼻和双耳几何对位门禁。"""

from __future__ import annotations

import math

from tools.capture_live2d_presets import (
    ear_pair_symmetry_metrics,
    nose_alignment_error_css_px,
)


def _status(*, nose_x: float, nose_y: float) -> dict[str, object]:
    """构造只含一个鼻部配件的最小运行状态。"""

    return {
        "accessories": {
            "binding": {"currentNose": {"x": 210.0, "y": 177.5}},
            "layers": [
                {
                    "role": "pig_nose",
                    "pinTo": "nose",
                    # 内部 position 故意保持正确；门禁必须检查浏览器最终合成
                    # 后的 DOM bounds，才能发现 CSS 或 transform 引入的偏差。
                    "position": {"x": 210.0, "y": 177.5},
                    "bounds": {
                        "x": nose_x - 6.0,
                        "y": nose_y - 4.0,
                        "width": 12.0,
                        "height": 8.0,
                    },
                }
            ],
        }
    }


def test_nose_alignment_error_is_zero_for_part_center_lock() -> None:
    """配件中心与 Part56 当前中心相同时误差必须为零。"""

    assert nose_alignment_error_css_px(_status(nose_x=210.0, nose_y=177.5)) == 0


def test_nose_alignment_error_preserves_visible_fixed_offset() -> None:
    """固定偏右应被量化，不能因绑定状态为 bound 而漏检。"""

    error = nose_alignment_error_css_px(_status(nose_x=213.8, nose_y=177.5))
    assert round(error, 6) == 3.8


def test_nose_alignment_error_rejects_missing_current_geometry() -> None:
    """声明了鼻部配件但没有实时网格中心时必须作为失败处理。"""

    status = _status(nose_x=210.0, nose_y=177.5)
    status["accessories"]["binding"].pop("currentNose")
    assert math.isinf(nose_alignment_error_css_px(status))


def _ear_status(*, midpoint_offset: float = 0.0) -> dict[str, object]:
    """构造相对鼻中轴左右对称或整体偏移的猪耳状态。"""

    nose_x = 210.0
    pair_midpoint = nose_x + midpoint_offset
    return {
        "accessories": {
            "binding": {"currentNose": {"x": nose_x, "y": 177.5}},
            "layers": [
                {
                    "role": "pig_ear_left",
                    "bounds": {
                        "x": pair_midpoint - 56.0,
                        "y": 35.0,
                        "width": 20.0,
                        "height": 18.0,
                    },
                },
                {
                    "role": "pig_ear_right",
                    "bounds": {
                        "x": pair_midpoint + 36.0,
                        "y": 35.0,
                        "width": 20.0,
                        "height": 18.0,
                    },
                },
            ],
        }
    }


def test_ear_pair_symmetry_is_centered_on_live_nose_axis() -> None:
    """左右耳中心的中点必须精确落在本帧鼻部中轴。"""

    metrics = ear_pair_symmetry_metrics(_ear_status())
    assert metrics is not None
    assert metrics["pair_midpoint_offset_css_px"] == 0
    assert metrics["pair_midpoint_error_css_px"] == 0
    assert metrics["center_y_difference_css_px"] == 0
    assert metrics["width_difference_css_px"] == 0
    assert metrics["height_difference_css_px"] == 0


def test_ear_pair_symmetry_preserves_group_offset() -> None:
    """两耳一起偏右时必须报告真实误差，不能被左右等距抵消。"""

    metrics = ear_pair_symmetry_metrics(_ear_status(midpoint_offset=3.2))
    assert metrics is not None
    assert round(metrics["pair_midpoint_offset_css_px"], 6) == 3.2
    assert round(metrics["pair_midpoint_error_css_px"], 6) == 3.2


def test_ear_pair_symmetry_rejects_incomplete_pair() -> None:
    """声明猪耳但缺失其中一只时必须返回无穷误差。"""

    status = _ear_status()
    status["accessories"]["layers"].pop()
    metrics = ear_pair_symmetry_metrics(status)
    assert metrics is not None
    assert math.isinf(metrics["pair_midpoint_error_css_px"])
