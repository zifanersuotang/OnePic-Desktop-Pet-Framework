"""在真实 PetWindow 中验证空闲定时器会自动闪现一个 Live2D 微表情。

烟测不发送鼠标或键盘事件；它缩短一次 QTimer 等待，并通过 WebGL API 回执、
显式 renderNow 与脸部像素差共同验证表情确实落屏，而不只检查 Python 请求状态。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageStat
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.window import PetWindow


def parse_args() -> argparse.Namespace:
    """读取烟测输入输出路径和真实显示高度。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--display-height", type=int, default=420)
    parser.add_argument("--timeout-ms", type=int, default=12_000)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    """返回截图 SHA-256，证明三个阶段确实各自产生文件。"""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _visible_pixels(path: Path) -> int:
    """返回透明截图中的可见像素数。"""

    with Image.open(path).convert("RGBA") as image:
        alpha = image.getchannel("A")
        return image.width * image.height - alpha.histogram()[0]


def _face_difference(baseline: Path, active: Path) -> dict[str, object]:
    """量化角色上半部的有效像素变化，避免“请求成功但画面未变”假阳性。"""

    if not baseline.is_file() or not active.is_file():
        return {
            "crop": None,
            "mean_absolute_rgb": 0.0,
            "changed_pixel_ratio_gt_10": 0.0,
            "passed": False,
        }
    with Image.open(baseline).convert("RGBA") as baseline_image:
        with Image.open(active).convert("RGBA") as active_image:
            if baseline_image.size != active_image.size:
                return {
                    "crop": None,
                    "mean_absolute_rgb": 0.0,
                    "changed_pixel_ratio_gt_10": 0.0,
                    "passed": False,
                }
            alpha_union = ImageChops.lighter(
                baseline_image.getchannel("A"),
                active_image.getchannel("A"),
            )
            bounds = alpha_union.getbbox()
            if bounds is None:
                return {
                    "crop": None,
                    "mean_absolute_rgb": 0.0,
                    "changed_pixel_ratio_gt_10": 0.0,
                    "passed": False,
                }
            left, top, right, bottom = bounds
            width = right - left
            height = bottom - top
            crop = (
                left + round(width * 0.08),
                top + round(height * 0.05),
                right - round(width * 0.08),
                top + round(height * 0.58),
            )
            baseline_face = baseline_image.crop(crop).convert("RGB")
            active_face = active_image.crop(crop).convert("RGB")
            difference = ImageChops.difference(baseline_face, active_face)
            grayscale_histogram = difference.convert("L").histogram()
            pixel_count = max(1, difference.width * difference.height)
            changed_ratio = sum(grayscale_histogram[11:]) / pixel_count
            mean_rgb = sum(ImageStat.Stat(difference).mean) / 3
            return {
                "crop": list(crop),
                "mean_absolute_rgb": round(mean_rgb, 6),
                "changed_pixel_ratio_gt_10": round(changed_ratio, 6),
                "minimum_mean_absolute_rgb": 1.2,
                "minimum_changed_pixel_ratio_gt_10": 0.03,
                "passed": bool(mean_rgb >= 1.2 and changed_ratio >= 0.03),
            }


def main() -> int:
    """运行一次无点击的自动微表情完整生命周期并输出证据报告。"""

    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    rotation = manifest.get("companion_behaviors", {}).get(
        "auto_expression_rotation",
        {},
    )
    if not isinstance(rotation, dict) or not rotation.get("enabled", False):
        raise ValueError("运行清单没有启用 auto_expression_rotation")
    sequence = str(rotation.get("sequence", "")).strip()
    extension = manifest.get("extension_animations", {}).get(sequence, {})
    presentation = manifest.get("hybrid_runtime", {}).get(
        "live2d_state_map",
        {},
    ).get(f"extension:{sequence}", {})
    expressions = presentation.get("expressions", [])
    frames = extension.get("frames", [])
    if (
        not sequence
        or not isinstance(frames, list)
        or not isinstance(expressions, list)
        or len(frames) != len(expressions)
        or not frames
    ):
        raise ValueError("自动微表情序列与 Live2D expressions 映射不完整")

    os.environ["ONEPIC_PET_MANIFEST"] = str(manifest_path)
    os.environ.pop("ONEPIC_USE_DEMO_ASSETS", None)
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    captures = {
        "baseline": args.capture_dir / "01-idle-baseline.png",
        "active": args.capture_dir / "02-auto-expression.png",
        "returned": args.capture_dir / "03-returned-idle.png",
    }
    for path in captures.values():
        path.unlink(missing_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)
    window = PetWindow(PetSettings(display_height=args.display_height))
    window.place_at_start()
    window.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    window.setEnabled(False)
    window.show()
    state: dict[str, object] = {
        "finished": False,
        "success": False,
        "error": "",
        "selected_frame_index": None,
        "selected_expression": None,
        "active_presentation": {},
        "returned_presentation": {},
        "baseline_render_status": {},
        "active_render_status": {},
        "returned_render_status": {},
        "active_query_pending": False,
        "returned_query_pending": False,
        "last_interaction_unchanged": False,
        "next_timer_active": False,
    }
    active_deadline = 0.0
    return_deadline = 0.0
    interaction_marker = 0.0

    def finish(success: bool, error: str = "") -> None:
        if state["finished"]:
            return
        state["finished"] = True
        state["success"] = success
        state["error"] = error
        app.quit()

    def grab(path: Path) -> bool:
        """保存真实透明桌宠窗口，而非只截 WebGL 子视图。"""

        return bool(window.grab().save(str(path), "PNG"))

    def query_render_status(
        callback: Callable[[dict[str, object]], None],
    ) -> None:
        """按 JS 队列顺序显式渲染，并把真正应用后的状态回传 Python。"""

        view = window._live2d_view
        if view is None:
            callback({"ok": False, "error": "missing-live2d-view"})
            return
        script = (
            "(() => {"
            "const api=window.onepicLive2D;"
            "if(!api){return JSON.stringify({ok:false,error:'missing-api'});}"
            "const rendered=api.renderNow();"
            "const status=api.getStatus();"
            "return JSON.stringify({"
            "ok:rendered===true,"
            "activeExpression:status.activeExpression ?? null,"
            "presentationRevision:status.presentationRevision,"
            "renderedPresentationRevision:status.renderedPresentationRevision,"
            "explicitRenderCount:status.explicitRenderCount,"
            "modelVisible:status.modelVisible,"
            "modelRenderable:status.modelRenderable"
            "});"
            "})()"
        )

        def accepted(result: object) -> None:
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    result = {"ok": False, "error": "invalid-json-receipt"}
            callback(result if isinstance(result, dict) else {"ok": False})

        view.page().runJavaScript(script, accepted)

    def poll_returned() -> None:
        if state["finished"]:
            return
        if window._extension_active_name is None:
            if state["returned_query_pending"]:
                return
            state["returned_query_pending"] = True
            view = window._live2d_view
            diagnostics = getattr(view, "diagnostics", {}) if view else {}
            returned = diagnostics.get("last_presentation", {})
            state["returned_presentation"] = returned

            def after_returned_render(status: dict[str, object]) -> None:
                state["returned_render_status"] = status
                revision = status.get("presentationRevision")
                rendered_revision = status.get("renderedPresentationRevision")
                returned_ok = bool(
                    status.get("ok")
                    and status.get("modelVisible")
                    and status.get("activeExpression") is None
                    and isinstance(revision, (int, float))
                    and rendered_revision == revision
                    and isinstance(returned, dict)
                    and "expression" not in returned
                )

                def capture_returned() -> None:
                    if not grab(captures["returned"]):
                        finish(False, "恢复待机截图失败")
                        return
                    finish(
                        returned_ok,
                        "微表情结束后 WebGL 未实际渲染中性待机"
                        if not returned_ok
                        else "",
                    )

                QTimer.singleShot(80, capture_returned)

            query_render_status(after_returned_render)
            return
        if time.monotonic() >= return_deadline:
            finish(False, "随机单帧超过配置时长仍未结束")
            return
        QTimer.singleShot(40, poll_returned)

    def poll_active() -> None:
        nonlocal return_deadline
        if state["finished"]:
            return
        if window._extension_active_name == sequence:
            if state["active_query_pending"]:
                return
            index = int(window._extension_frame_index)
            if not 0 <= index < len(expressions):
                finish(False, f"自动微表情帧索引越界：{index}")
                return
            view = window._live2d_view
            diagnostics = getattr(view, "diagnostics", {}) if view else {}
            active = diagnostics.get("last_presentation", {})
            expected_expression = str(expressions[index])
            state["selected_frame_index"] = index
            state["selected_expression"] = expected_expression
            state["active_presentation"] = active
            state["last_interaction_unchanged"] = (
                window._last_user_interaction == interaction_marker
            )
            state["next_timer_active"] = window.auto_expression_timer.isActive()
            if (
                not window._extension_single_frame
                or not isinstance(active, dict)
                or active.get("expression") != expected_expression
            ):
                finish(False, "自动轮动没有映射到选中的单个 Live2D 表情")
                return
            state["active_query_pending"] = True

            def after_active_render(status: dict[str, object]) -> None:
                nonlocal return_deadline
                state["active_render_status"] = status
                revision = status.get("presentationRevision")
                rendered_revision = status.get("renderedPresentationRevision")
                if not (
                    status.get("ok")
                    and status.get("modelVisible")
                    and status.get("activeExpression") == expected_expression
                    and isinstance(revision, (int, float))
                    and rendered_revision == revision
                ):
                    finish(False, "Live2D WebGL 未确认所选表情已经实际渲染")
                    return

                def capture_active() -> None:
                    nonlocal return_deadline
                    if window._extension_active_name != sequence:
                        finish(False, "WebGL 回执前随机微表情已提前结束")
                        return
                    if not grab(captures["active"]):
                        finish(False, "自动微表情截图失败")
                        return
                    duration = rotation.get("duration_ms", [1200, 1800])
                    maximum_duration = (
                        max(int(value) for value in duration)
                        if isinstance(duration, list) and duration
                        else 1800
                    )
                    return_deadline = (
                        time.monotonic() + (maximum_duration + 1200) / 1000
                    )
                    QTimer.singleShot(40, poll_returned)

                QTimer.singleShot(120, capture_active)

            query_render_status(after_active_render)
            return
        if time.monotonic() >= active_deadline:
            finish(False, "无点击定时器未触发自动微表情")
            return
        QTimer.singleShot(40, poll_active)

    def start_rotation() -> None:
        nonlocal active_deadline, interaction_marker

        def after_baseline_render(status: dict[str, object]) -> None:
            nonlocal active_deadline, interaction_marker
            state["baseline_render_status"] = status
            if not (
                status.get("ok")
                and status.get("modelVisible")
                and status.get("activeExpression") is None
            ):
                finish(False, "待机基准没有得到中性 WebGL 渲染回执")
                return
            if not grab(captures["baseline"]):
                finish(False, "待机基准截图失败")
                return
            # 强制最近窗口覆盖前 N-1 帧，仅让最后一个已知表情可选；
            # 这样烟测稳定验证同一个明显表情，正式配置仍保持随机轮动。
            runtime_rotation = window._companion_config[
                "auto_expression_rotation"
            ]
            runtime_rotation["no_repeat_count"] = max(0, len(frames) - 1)
            window._auto_expression_recent.clear()
            window._auto_expression_recent.extend(range(max(0, len(frames) - 1)))
            minimum_idle_ms = max(
                0,
                int(rotation.get("minimum_idle_ms", 2600)),
            )
            window._last_user_interaction = (
                time.monotonic() - (minimum_idle_ms + 1000) / 1000
            )
            interaction_marker = window._last_user_interaction
            active_deadline = time.monotonic() + 2.5
            # 仍由正式 QTimer.timeout 信号触发；这里只缩短本次烟测等待。
            window.auto_expression_timer.start(250)
            QTimer.singleShot(40, poll_active)

        query_render_status(after_baseline_render)

    def prepare() -> None:
        if state["finished"]:
            return
        if not window._live2d_ready:
            QTimer.singleShot(100, prepare)
            return
        window.state_timer.stop()
        window.movement_timer.stop()
        window.interaction_timer.stop()
        window.touch_timer.stop()
        window.autoplay_timer.stop()
        window.auto_expression_timer.stop()
        window.keyboard_timer.stop()
        window.set_state(PetState.IDLE)
        window.state_timer.stop()
        QTimer.singleShot(300, start_rotation)

    QTimer.singleShot(100, prepare)
    QTimer.singleShot(args.timeout_ms, lambda: finish(False, "自动微表情烟测超时"))
    app.exec()

    capture_report: dict[str, object] = {}
    for name, path in captures.items():
        capture_report[name] = {
            "path": str(path.resolve()),
            "exists": path.is_file(),
            "sha256": _sha256(path) if path.is_file() else None,
            "visible_pixels": _visible_pixels(path) if path.is_file() else 0,
        }
    visual_difference = _face_difference(
        captures["baseline"],
        captures["active"],
    )
    passed = bool(
        state["success"]
        and state["last_interaction_unchanged"]
        and state["next_timer_active"]
        and visual_difference["passed"]
        and all(
            int(record["visible_pixels"]) >= 500
            for record in capture_report.values()
        )
    )
    report = {
        "status": "passed" if passed else "failed",
        "manifest": str(manifest_path),
        "display_height": args.display_height,
        "sequence": sequence,
        "configured_expression_count": len(expressions),
        "selected_frame_index": state["selected_frame_index"],
        "selected_expression": state["selected_expression"],
        "active_presentation": state["active_presentation"],
        "returned_presentation": state["returned_presentation"],
        "baseline_render_status": state["baseline_render_status"],
        "active_render_status": state["active_render_status"],
        "returned_render_status": state["returned_render_status"],
        "last_interaction_unchanged": state["last_interaction_unchanged"],
        "next_timer_active": state["next_timer_active"],
        "face_visual_difference": visual_difference,
        "error": state["error"],
        "captures": capture_report,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    window.close()
    window.deleteLater()
    app.processEvents()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
