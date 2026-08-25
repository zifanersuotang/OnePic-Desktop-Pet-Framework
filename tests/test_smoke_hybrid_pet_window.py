"""验证 PetWindow Live2D 烟测的眨眼与自主视线/头动发布边界。"""

from tools.smoke_hybrid_pet_window import (
    _idle_capture_status_matches,
    _lively_motion_sample,
    _lively_motion_sample_changed,
    _summarize_lively_motion_samples,
)


def test_idle_capture_requires_complete_closed_blink_when_enabled() -> None:
    """启用眨眼时只有完整闭眼、眼区遮罩和隐藏底模同时成立才可捕获。"""

    status = {
        "ready": True,
        "blinkCount": 1,
        "eyeOpen": 0.0,
        "headOpenVisible": True,
        "headClosedVisible": True,
        "headClosedAlpha": 1.0,
        "blinkClosure": 1.0,
        "blinkRegionCount": 2,
        "modelVisible": False,
        "modelRenderable": False,
    }

    assert _idle_capture_status_matches(status, blink_enabled=True)
    assert not _idle_capture_status_matches(status, blink_enabled=False)


def test_idle_capture_accepts_stable_open_pose_when_blink_is_disabled() -> None:
    """关闭眨眼时允许稳定睁眼，但闭眼层误显或底模可见仍必须失败。"""

    status = {
        "ready": True,
        "blinkCount": 0,
        "eyeOpen": 1.0,
        "headOpenVisible": True,
        "headClosedVisible": False,
        "blinkClosure": 0.0,
        "modelVisible": False,
        "modelRenderable": False,
    }

    assert _idle_capture_status_matches(status, blink_enabled=False)
    status["headClosedVisible"] = True
    assert not _idle_capture_status_matches(status, blink_enabled=False)


def test_idle_capture_accepts_cubism_parameter_blink() -> None:
    """真实 Cubism 后端用参数端点判定闭眼，不依赖分层遮罩字段。"""

    status = {
        "ready": True,
        "renderer": "cubism-parameters",
        "blinkCount": 1,
        "eyeOpen": 0.0,
        "modelVisible": True,
        "modelRenderable": False,
        "headOpenVisible": None,
        "headClosedVisible": None,
    }

    assert _idle_capture_status_matches(status, blink_enabled=True)
    status["eyeOpen"] = 1.0
    assert not _idle_capture_status_matches(status, blink_enabled=True)


def test_lively_motion_sample_compacts_unchanged_runtime_status() -> None:
    """相同索引和近似坐标不重复入库，索引或明显坐标变化才保留。"""

    status = {
        "ready": True,
        "renderer": "cubism-parameters",
        "gazeMoveCount": 1,
        "currentGaze": {"index": 1, "x": -0.72, "y": 0.62},
        "currentHead": {"index": 1, "x": -0.78, "y": 0.12, "z": -0.72},
    }
    first = _lively_motion_sample(status)

    assert first is not None
    assert _lively_motion_sample_changed(None, first)
    status["currentGaze"] = {"index": 1, "x": -0.718, "y": 0.615}
    almost_same = _lively_motion_sample(status)
    assert almost_same is not None
    assert not _lively_motion_sample_changed(first, almost_same)
    status["currentGaze"] = {"index": 2, "x": -0.04, "y": 0.06}
    changed = _lively_motion_sample(status)
    assert changed is not None
    assert _lively_motion_sample_changed(first, changed)


def test_lively_motion_gate_accepts_three_targets_and_required_spans() -> None:
    """Cubism 开启视线和头动时，计数、索引数与坐标跨度均达标才通过。"""

    samples = [
        {
            "gaze_move_count": 0,
            "gaze": {"index": 0, "x": 0.06, "y": 0.03},
            "head": {"index": 5, "x": 0.42, "y": 0.04, "z": 0.30},
        },
        {
            "gaze_move_count": 1,
            "gaze": {"index": 1, "x": -0.72, "y": 0.62},
            "head": {"index": 0, "x": 0.04, "y": 0.02, "z": 0.0},
        },
        {
            "gaze_move_count": 2,
            "gaze": {"index": 2, "x": -0.04, "y": 0.06},
            "head": {"index": 1, "x": -0.78, "y": 0.12, "z": -0.72},
        },
    ]

    summary = _summarize_lively_motion_samples(
        samples,
        renderer="cubism-parameters",
        eye_motion_enabled=True,
        head_motion_enabled=True,
    )

    assert summary["passed"] is True
    assert summary["gaze_move_count"] == 2
    assert summary["gaze_index_count"] == 3
    assert summary["gaze_y_span"] == 0.59
    assert summary["head_index_count"] == 3
    assert summary["head_x_span"] == 1.2


def test_lively_motion_gate_rejects_static_enabled_cubism_status() -> None:
    """只有开关而目标不切换时，真实 Cubism 发布门禁必须失败。"""

    samples = [
        {
            "gaze_move_count": 0,
            "gaze": {"index": 0, "x": 0.06, "y": 0.03},
            "head": {"index": 0, "x": 0.04, "y": 0.02, "z": 0.0},
        }
    ]

    summary = _summarize_lively_motion_samples(
        samples,
        renderer="cubism-parameters",
        eye_motion_enabled=True,
        head_motion_enabled=True,
    )

    assert summary["eye_motion_passed"] is False
    assert summary["head_motion_passed"] is False
    assert summary["passed"] is False


def test_lively_motion_gate_skips_disabled_or_non_cubism_backends() -> None:
    """关闭相应开关或使用分层后端时，不因缺少 Cubism 目标样本误失败。"""

    disabled = _summarize_lively_motion_samples(
        [],
        renderer="cubism-parameters",
        eye_motion_enabled=False,
        head_motion_enabled=False,
    )
    layered = _summarize_lively_motion_samples(
        [],
        renderer="layered-webgl",
        eye_motion_enabled=True,
        head_motion_enabled=True,
    )

    assert disabled["passed"] is True
    assert layered["passed"] is True
    assert layered["eye_motion_expected"] is False
    assert layered["head_motion_expected"] is False
