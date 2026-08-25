"""在真实 PetWindow 中验证 Live2D 待机、灵动视线、头动、眨眼与合成。

同时支持真实 Cubism 参数渲染和分层 WebGL 安全后端，避免把后端字段差异
误判成透明帧、眨眼或自主运动失败；Cubism 后端还会在 4.8 秒窗口内采样
视线与头部目标，阻止只有配置开关而没有真实运行变化的交付包发布。报告带稳定
schema 版本，供最终发布取证读取。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.window import PetWindow


def _file_sha256(path: Path) -> str:
    """计算被测 runtime manifest 的 SHA-256。"""

    return hashlib.sha256(path.read_bytes()).hexdigest()


MINIMUM_GAZE_MOVE_COUNT = 2
MINIMUM_GAZE_INDEX_COUNT = 3
MINIMUM_GAZE_Y_SPAN = 0.15
MINIMUM_HEAD_INDEX_COUNT = 3
MINIMUM_HEAD_X_SPAN = 0.15
LIVELY_SAMPLE_CHANGE_EPSILON = 0.015


def _motion_point(
    value: object,
    *,
    axes: tuple[str, ...],
) -> dict[str, int | float] | None:
    """把运行态目标点压成可序列化的有限数字，缺字段时安全忽略。"""

    if not isinstance(value, dict):
        return None
    try:
        index = int(value["index"])
        coordinates = {axis: float(value[axis]) for axis in axes}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(number) for number in coordinates.values()):
        return None
    return {
        "index": index,
        **{axis: round(number, 6) for axis, number in coordinates.items()},
    }


def _lively_motion_sample(
    status: dict[str, object],
) -> dict[str, object] | None:
    """从一次 Cubism 状态轮询提取精简视线/头动样本。"""

    if (
        status.get("renderer") != "cubism-parameters"
        or not bool(status.get("ready"))
    ):
        return None
    gaze = _motion_point(status.get("currentGaze"), axes=("x", "y"))
    head = _motion_point(status.get("currentHead"), axes=("x", "y", "z"))
    if gaze is None and head is None:
        return None
    try:
        gaze_move_count = max(0, int(status.get("gazeMoveCount", 0)))
    except (TypeError, ValueError, OverflowError):
        gaze_move_count = 0
    return {
        "gaze_move_count": gaze_move_count,
        "gaze": gaze,
        "head": head,
    }


def _lively_motion_sample_changed(
    previous: dict[str, object] | None,
    current: dict[str, object],
) -> bool:
    """仅保留目标索引、计数或坐标发生有意义变化的运行态样本。"""

    if previous is None:
        return True
    if previous.get("gaze_move_count") != current.get("gaze_move_count"):
        return True
    for name, axes in (("gaze", ("x", "y")), ("head", ("x", "y", "z"))):
        old_point = previous.get(name)
        new_point = current.get(name)
        if not isinstance(old_point, dict) or not isinstance(new_point, dict):
            if old_point != new_point:
                return True
            continue
        if old_point.get("index") != new_point.get("index"):
            return True
        if any(
            abs(float(old_point[axis]) - float(new_point[axis]))
            >= LIVELY_SAMPLE_CHANGE_EPSILON
            for axis in axes
        ):
            return True
    return False


def _summarize_lively_motion_samples(
    samples: list[dict[str, object]],
    *,
    renderer: str,
    eye_motion_enabled: bool,
    head_motion_enabled: bool,
) -> dict[str, object]:
    """汇总自主运动采样并给出不会误伤其他后端的发布门禁结果。"""

    cubism_renderer = renderer == "cubism-parameters"
    eye_motion_expected = cubism_renderer and eye_motion_enabled
    head_motion_expected = cubism_renderer and head_motion_enabled
    gaze_points = [
        sample["gaze"]
        for sample in samples
        if isinstance(sample.get("gaze"), dict)
    ]
    head_points = [
        sample["head"]
        for sample in samples
        if isinstance(sample.get("head"), dict)
    ]
    gaze_indexes = sorted({int(point["index"]) for point in gaze_points})
    head_indexes = sorted({int(point["index"]) for point in head_points})
    gaze_y_values = [float(point["y"]) for point in gaze_points]
    head_x_values = [float(point["x"]) for point in head_points]
    gaze_y_span = (
        max(gaze_y_values) - min(gaze_y_values) if gaze_y_values else 0.0
    )
    head_x_span = (
        max(head_x_values) - min(head_x_values) if head_x_values else 0.0
    )
    gaze_move_count = max(
        (int(sample.get("gaze_move_count", 0)) for sample in samples),
        default=0,
    )
    eye_motion_passed = (
        not eye_motion_expected
        or (
            gaze_move_count >= MINIMUM_GAZE_MOVE_COUNT
            and len(gaze_indexes) >= MINIMUM_GAZE_INDEX_COUNT
            and gaze_y_span >= MINIMUM_GAZE_Y_SPAN
        )
    )
    head_motion_passed = (
        not head_motion_expected
        or (
            len(head_indexes) >= MINIMUM_HEAD_INDEX_COUNT
            and head_x_span >= MINIMUM_HEAD_X_SPAN
        )
    )
    return {
        "renderer": renderer,
        "sample_count": len(samples),
        "eye_motion_expected": eye_motion_expected,
        "head_motion_expected": head_motion_expected,
        "gaze_move_count": gaze_move_count,
        "gaze_indexes": gaze_indexes,
        "gaze_index_count": len(gaze_indexes),
        "gaze_y_span": round(gaze_y_span, 6),
        "head_indexes": head_indexes,
        "head_index_count": len(head_indexes),
        "head_x_span": round(head_x_span, 6),
        "thresholds": {
            "minimum_gaze_move_count": MINIMUM_GAZE_MOVE_COUNT,
            "minimum_gaze_index_count": MINIMUM_GAZE_INDEX_COUNT,
            "minimum_gaze_y_span": MINIMUM_GAZE_Y_SPAN,
            "minimum_head_index_count": MINIMUM_HEAD_INDEX_COUNT,
            "minimum_head_x_span": MINIMUM_HEAD_X_SPAN,
        },
        "eye_motion_passed": eye_motion_passed,
        "head_motion_passed": head_motion_passed,
        "passed": eye_motion_passed and head_motion_passed,
    }


def _idle_capture_status_matches(
    status: dict[str, object],
    *,
    blink_enabled: bool,
) -> bool:
    """判断当前 Live2D 状态是否适合捕获已配置的待机眼部姿态。"""

    if not bool(status.get("ready")):
        return False
    renderer = str(status.get("renderer", ""))
    if renderer == "cubism-parameters":
        if not bool(status.get("modelVisible", False)):
            return False
        if blink_enabled:
            return (
                int(status.get("blinkCount", 0)) >= 1
                and float(status.get("eyeOpen", 1)) <= 0.02
            )
        return (
            int(status.get("blinkCount", 0)) == 0
            and float(status.get("eyeOpen", 0)) >= 0.98
        )
    if renderer not in {"", "layered-webgl"}:
        return False
    if (
        not bool(status.get("headOpenVisible", False))
        or bool(status.get("modelVisible", True))
        or bool(status.get("modelRenderable", True))
    ):
        return False
    if blink_enabled:
        return (
            int(status.get("blinkCount", 0)) >= 1
            and float(status.get("eyeOpen", 1)) <= 0.02
            and bool(status.get("headClosedVisible", False))
            and float(status.get("headClosedAlpha", 0)) >= 0.98
            and float(status.get("blinkClosure", 0)) >= 0.98
            and int(status.get("blinkRegionCount", 0)) >= 2
        )
    return (
        int(status.get("blinkCount", 0)) == 0
        and float(status.get("eyeOpen", 0)) >= 0.98
        and not bool(status.get("headClosedVisible", True))
        and float(status.get("blinkClosure", 1)) <= 0.02
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=None)
    parser.add_argument(
        "--display-height",
        type=int,
        default=360,
        help="按实际用户显示高度复现缩放和窗口裁切，默认 360",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    hybrid = manifest.get("hybrid_runtime", {})
    animation = hybrid.get("idle_animation", {})
    animation_enabled = bool(animation.get("enabled", True))
    blink_enabled = bool(animation.get("blink", True))
    eye_motion_enabled = animation_enabled and bool(
        animation.get("eye_motion", True)
    )
    head_motion_enabled = animation_enabled and bool(
        animation.get("head_motion", True)
    )
    intervals = animation.get("blink_interval_ms", [2600, 4600])
    durations = animation.get("blink_duration_ms", [105, 60, 160])
    required_idle_ms = (
        int(intervals[1]) + sum(int(value) for value in durations) + 500
        if blink_enabled
        else 5300
    )
    timeout_ms = args.timeout_ms or required_idle_ms + 5000

    os.environ["ONEPIC_PET_MANIFEST"] = str(manifest_path)
    os.environ.pop("ONEPIC_USE_DEMO_ASSETS", None)
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    blink_path = args.capture_dir / "pet-window-automatic-blink.png"
    view_blink_path = args.capture_dir / "live2d-view-automatic-blink.png"
    blink_path.unlink(missing_ok=True)
    view_blink_path.unlink(missing_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)
    window = PetWindow(PetSettings(display_height=args.display_height))
    # 与正式应用一致先放到屏幕右下角，避免测试窗口默认出现在鼠标下方，
    # 被被动摸头识别为 happy 而干扰自主 idle 验证。
    window.place_at_start()
    # 本烟测只验证无交互自主状态；屏蔽测试期间真实鼠标移动/按键，避免外部
    # 输入把状态切成 drag、happy 或 curious。
    window.setAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents,
        True,
    )
    window.setEnabled(False)
    window.show()
    state: dict[str, object] = {
        "finished": False,
        "left_idle": False,
        "last_status": {},
        "motion_scheduled": False,
        "motion_samples_complete": False,
        "motion_sample_paths": [],
        "idle_hold_started": False,
        "blink_captured": False,
        "blink_scheduled": False,
        "idle_capture_matched": False,
        "eye_open_samples": [],
        "lively_motion_samples": [],
    }

    def finish(success: bool) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        state["success"] = success
        app.quit()

    def finish_if_complete() -> None:
        if state["blink_captured"] and state["motion_samples_complete"]:
            finish(True)

    def capture_blink() -> None:
        view = window._live2d_view
        if (
            view is None
            or not view.grab().save(str(view_blink_path), "PNG")
            or not window.grab().save(str(blink_path), "PNG")
        ):
            finish(False)
            return
        state["blink_captured"] = True
        finish_if_complete()

    def schedule_idle_capture(status: dict[str, object]) -> None:
        """在 4.8 秒自主运动窗口结束后稳定提交待机眼部截图。"""

        if state["blink_scheduled"]:
            return
        state["blink_scheduled"] = True
        view = window._live2d_view
        if view is None:
            finish(False)
            return
        if status.get("renderer") == "cubism-parameters" and blink_enabled:
            # 自动眨眼事件已经发生；冻结同一闭眼端点再截图，避免短暂停留段
            # 在异步 grab 提交前结束。关闭眨眼时则直接保留稳定睁眼截图。
            view.page().runJavaScript(
                "window.onepicLive2D.setPaused(true);"
                "window.onepicLive2D.setParameters({"
                "ParamEyeLOpen:0,ParamEyeROpen:0});",
                lambda _result: QTimer.singleShot(80, capture_blink),
            )
            return
        QTimer.singleShot(0, capture_blink)

    def capture_motion_sample(index: int, final_sample: bool) -> None:
        view = window._live2d_view
        view_path = args.capture_dir / f"root-motion-view-{index:02d}.png"
        window_path = args.capture_dir / f"root-motion-window-{index:02d}.png"
        if (
            view is None
            or not view.grab().save(str(view_path), "PNG")
            or not window.grab().save(str(window_path), "PNG")
        ):
            finish(False)
            return
        state["motion_sample_paths"].append((view_path, window_path))
        if final_sample:
            state["motion_samples_complete"] = True
            if state["idle_capture_matched"]:
                schedule_idle_capture(dict(state["last_status"]))
            finish_if_complete()

    def schedule_motion_samples() -> None:
        if state["motion_scheduled"]:
            return
        state["motion_scheduled"] = True
        # 根运动的呼吸周期约 4.34 秒；覆盖一整周期才能检查上移/放大峰值。
        # ready 信号只表示资源可用；给 WebGL 300 毫秒提交首帧，避免把尚未
        # 绘制的透明画布误判为角色消失。末张仍跨过完整 4.34 秒周期。
        sample_offsets_ms = (300, 1050, 1800, 2550, 3300, 4050, 4800)
        for index, offset_ms in enumerate(sample_offsets_ms):
            QTimer.singleShot(
                offset_ms,
                lambda value=index, final=index == len(sample_offsets_ms) - 1:
                    capture_motion_sample(value, final),
            )

    def accept_status(result: object) -> None:
        try:
            status = json.loads(result) if isinstance(result, str) else {}
        except json.JSONDecodeError:
            status = {}
        state["last_status"] = status
        if status.get("renderer") == "cubism-parameters":
            state["eye_open_samples"].append(
                round(float(status.get("eyeOpen", 1)), 4)
            )
            lively_sample = _lively_motion_sample(status)
            lively_samples = state["lively_motion_samples"]
            if (
                lively_sample is not None
                and _lively_motion_sample_changed(
                    lively_samples[-1] if lively_samples else None,
                    lively_sample,
                )
            ):
                lively_samples.append(lively_sample)
        if window.state is not PetState.IDLE:
            state["left_idle"] = True
            finish(False)
            return
        if status.get("ready"):
            schedule_motion_samples()
            if not blink_enabled and not state["idle_hold_started"]:
                # ready 回调会按正式状态机重新启动当前状态计时器。关闭眨眼时，
                # 在 ready 之后仅为隔离烟测保持 5.3 秒 idle，保证七张采样覆盖
                # 完整根运动周期；不改变正式桌宠的日常状态时长。
                state["idle_hold_started"] = True
                window.state_timer.start(required_idle_ms)
        if _idle_capture_status_matches(
            status,
            blink_enabled=blink_enabled,
        ):
            state["idle_capture_matched"] = True
            cubism_lively_window_pending = (
                status.get("renderer") == "cubism-parameters"
                and (eye_motion_enabled or head_motion_enabled)
                and not state["motion_samples_complete"]
            )
            if not cubism_lively_window_pending:
                schedule_idle_capture(status)
            if state["blink_scheduled"]:
                return
        QTimer.singleShot(40, poll)

    def poll() -> None:
        if state["finished"]:
            return
        view = window._live2d_view
        if view is None:
            finish(False)
            return
        view.page().runJavaScript(
            "JSON.stringify(window.onepicLive2D.getStatus())",
            accept_status,
        )

    QTimer.singleShot(100, poll)
    QTimer.singleShot(timeout_ms, lambda: finish(False))
    app.exec()
    nontransparent_pixels = 0
    view_nontransparent_pixels = 0
    motion_samples: list[dict[str, object]] = []
    if blink_path.is_file():
        with Image.open(blink_path).convert("RGBA") as blink_image:
            histogram = blink_image.getchannel("A").histogram()
            nontransparent_pixels = (
                blink_image.width * blink_image.height - histogram[0]
            )
    if view_blink_path.is_file():
        with Image.open(view_blink_path).convert("RGBA") as view_blink_image:
            view_histogram = view_blink_image.getchannel("A").histogram()
            view_nontransparent_pixels = (
                view_blink_image.width
                * view_blink_image.height
                - view_histogram[0]
            )
    visible_coverage = (
        nontransparent_pixels / view_nontransparent_pixels
        if view_nontransparent_pixels
        else 0.0
    )
    for view_path, window_path in state["motion_sample_paths"]:
        with Image.open(view_path).convert("RGBA") as view_image:
            view_alpha = view_image.getchannel("A")
            view_bbox = view_alpha.getbbox()
            view_pixels = view_image.width * view_image.height
            view_opaque = view_pixels - view_alpha.histogram()[0]
        with Image.open(window_path).convert("RGBA") as window_image:
            window_alpha = window_image.getchannel("A")
            window_bbox = window_alpha.getbbox()
            window_pixels = window_image.width * window_image.height
            window_opaque = window_pixels - window_alpha.histogram()[0]
        sample_coverage = window_opaque / view_opaque if view_opaque else 0.0
        motion_samples.append(
            {
                "view": str(view_path.resolve()),
                "window": str(window_path.resolve()),
                "view_bbox": list(view_bbox) if view_bbox else None,
                "window_bbox": list(window_bbox) if window_bbox else None,
                "view_top_margin": view_bbox[1] if view_bbox else -1,
                "visible_coverage": round(sample_coverage, 6),
            }
        )
    minimum_top_margin = min(
        (int(sample["view_top_margin"]) for sample in motion_samples),
        default=-1,
    )
    minimum_motion_coverage = min(
        (float(sample["visible_coverage"]) for sample in motion_samples),
        default=0.0,
    )
    mask_is_full_window = window.mask() == QRegion(window.rect())
    unique_eye_open_samples = sorted(set(state["eye_open_samples"]))
    binary_blink_mode = (
        animation.get("blink_parameter_mode") == "binary"
    )
    binary_blink_valid = (
        not binary_blink_mode
        or set(unique_eye_open_samples).issubset({0.0, 1.0})
    )
    runtime_renderer = str(state["last_status"].get("renderer", ""))
    lively_motion_summary = _summarize_lively_motion_samples(
        state["lively_motion_samples"],
        renderer=runtime_renderer,
        eye_motion_enabled=eye_motion_enabled,
        head_motion_enabled=head_motion_enabled,
    )
    report = {
        "schema_version": 1,
        "status": (
            "passed"
            if state.get("success")
            and not state["left_idle"]
            and nontransparent_pixels >= 500
            and visible_coverage >= 0.98
            and len(motion_samples) == 7
            and minimum_top_margin >= 2
            and minimum_motion_coverage >= 0.98
            and mask_is_full_window
            and binary_blink_valid
            and lively_motion_summary["passed"]
            else "failed"
        ),
        "pet_id": manifest.get("pet_id"),
        "build_id": manifest.get("build_id"),
        "runtime_manifest_sha256": _file_sha256(manifest_path),
        "manifest": str(manifest_path),
        "blink_expected": blink_enabled,
        "idle_capture_mode": (
            "automatic_closed_blink" if blink_enabled else "stable_open_idle"
        ),
        "pet_state": window.state.value,
        "required_idle_ms": required_idle_ms,
        "scheduled_idle_ms": window.state_timer.interval(),
        "display_height": args.display_height,
        "left_idle_before_blink": state["left_idle"],
        "automatic_blink_nontransparent_pixels": nontransparent_pixels,
        "live2d_view_nontransparent_pixels": view_nontransparent_pixels,
        "pet_window_visible_coverage": round(visible_coverage, 6),
        "mask_is_full_window": mask_is_full_window,
        "blink_parameter_mode": animation.get(
            "blink_parameter_mode",
            "smooth",
        ),
        "eye_open_samples": state["eye_open_samples"],
        "unique_eye_open_samples": unique_eye_open_samples,
        "binary_blink_valid": binary_blink_valid,
        "lively_motion": lively_motion_summary,
        "lively_motion_samples": state["lively_motion_samples"],
        "minimum_root_motion_top_margin": minimum_top_margin,
        "minimum_root_motion_visible_coverage": round(
            minimum_motion_coverage,
            6,
        ),
        "root_motion_samples": motion_samples,
        "automatic_blink_status": state["last_status"],
        "captures": {
            "automatic_blink": str(blink_path.resolve()),
            "live2d_view_automatic_blink": str(view_blink_path.resolve()),
        },
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    window.close()
    window.deleteLater()
    app.processEvents()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
