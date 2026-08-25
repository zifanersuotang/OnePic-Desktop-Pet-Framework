"""验证分层 Live2D 宿主首帧、可见眨眼和独立运动，而非仅报告 ready。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops
from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.live2d_view import Live2DIdleView


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument(
        "--manifest-name",
        default="runtime-manifest.json",
        help="工作区使用 runtime-manifest.json，激活/打包目录使用 manifest.json",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--layer-manifest", required=True)
    parser.add_argument(
        "--pet-config",
        type=Path,
        required=True,
        help="从 pet-config.json 读取 hybrid_runtime.idle_animation",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=None,
        help="默认根据配置中的最长眨眼周期计算，并且不少于 20 秒",
    )
    return parser.parse_args()


def changed_pixels(first_path: Path, second_path: Path) -> int:
    """统计 RGB 差值超过抗锯齿噪声阈值的像素。"""

    with Image.open(first_path).convert("RGBA") as first:
        with Image.open(second_path).convert("RGBA") as second:
            difference = ImageChops.difference(first, second).convert("RGB")
            grayscale = difference.convert("L")
        return sum(grayscale.histogram()[3:])


def nontransparent_pixels(path: Path) -> int:
    """统计截图中非透明像素，避免把 ready 后尚未合成的空帧当作开眼基准。"""

    with Image.open(path).convert("RGBA") as image:
        histogram = image.getchannel("A").histogram()
        return image.width * image.height - histogram[0]


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pet_config = json.loads(
        args.pet_config.read_text(encoding="utf-8-sig")
    )
    profile = pet_config.get("hybrid_runtime", {}).get(
        "idle_animation"
    )
    if not isinstance(profile, dict):
        raise ValueError("pet-config.json 缺少 idle_animation 对象")
    blink_interval = profile.get("blink_interval_ms", [2600, 4600])
    blink_duration = profile.get("blink_duration_ms", [105, 60, 160])
    maximum_blink_interval = (
        int(blink_interval[1])
        if isinstance(blink_interval, list) and len(blink_interval) == 2
        else 4600
    )
    total_blink_duration = (
        sum(int(value) for value in blink_duration)
        if isinstance(blink_duration, list) and len(blink_duration) == 3
        else 325
    )
    timeout_ms = args.timeout_ms or max(
        20_000,
        maximum_blink_interval + total_blink_duration + 5_000,
    )
    automatic_poll_limit = max(
        180,
        (maximum_blink_interval + total_blink_duration + 2_000) // 30,
    )
    root = args.runtime_root.resolve()
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv)
    view = Live2DIdleView(
        root / args.manifest_name,
        {
            "model": args.model,
            "layer_manifest": args.layer_manifest,
            "idle_animation": profile,
        },
    )
    view.resize(420, 500)
    view.show()
    state: dict[str, object] = {
        "done": False,
        "open_capture_attempts": 0,
    }
    open_path = args.capture_dir / "eyes-open.png"
    closed_path = args.capture_dir / "eyes-closed.png"
    motion_start_path = args.capture_dir / "motion-start.png"
    motion_end_path = args.capture_dir / "motion-end.png"
    automatic_blink_path = args.capture_dir / "automatic-blink.png"
    state["automatic_poll_count"] = 0

    def finish(success: bool) -> None:
        if state["done"]:
            return
        state["done"] = True
        state["success"] = success
        app.quit()

    def save(path: Path) -> bool:
        return view.grab().save(str(path), "PNG")

    def poll_automatic_blink() -> None:
        state["automatic_poll_count"] = int(
            state["automatic_poll_count"]
        ) + 1

        def accept_status(result: object) -> None:
            try:
                status = (
                    json.loads(result)
                    if isinstance(result, str)
                    else {}
                )
            except json.JSONDecodeError:
                status = {}
            state["automatic_status"] = status
            if (
                int(status.get("blinkCount", 0)) >= 1
                and float(status.get("eyeOpen", 1)) <= 0.38
            ):
                # QWebEngine 的 grab 可能落后 JS/Pixi 一个合成帧；在闭眼停留段
                # 中部再抓图，避免把状态已闭眼但画面仍是前一帧误判为通过。
                QTimer.singleShot(90, capture_automatic_blink)
                return
            if int(state["automatic_poll_count"]) >= automatic_poll_limit:
                finish(False)
                return
            QTimer.singleShot(30, poll_automatic_blink)

        view.page().runJavaScript(
            "JSON.stringify(window.onepicLive2D.getStatus())",
            accept_status,
        )

    def capture_automatic_blink() -> None:
        if save(automatic_blink_path):
            finish(True)
        else:
            finish(False)

    def capture_motion_end() -> None:
        if not save(motion_end_path):
            finish(False)
            return
        poll_automatic_blink()

    def capture_motion_start() -> None:
        if not save(motion_start_path):
            finish(False)
            return
        QTimer.singleShot(1200, capture_motion_end)

    def capture_closed() -> None:
        if not save(closed_path):
            finish(False)
            return
        view.page().runJavaScript(
            "window.onepicLive2D.setLayeredBlink(false);"
            "window.onepicLive2D.setPaused(false)",
            lambda _result: QTimer.singleShot(100, capture_motion_start),
        )

    def capture_open() -> None:
        if not save(open_path):
            finish(False)
            return
        state["open_capture_attempts"] = int(
            state["open_capture_attempts"]
        ) + 1
        if (
            nontransparent_pixels(open_path) < 500
            and int(state["open_capture_attempts"]) < 6
        ):
            QTimer.singleShot(200, capture_open)
            return
        if nontransparent_pixels(open_path) < 500:
            finish(False)
            return
        view.page().runJavaScript(
            "window.onepicLive2D.setLayeredBlink(true)",
            lambda _result: QTimer.singleShot(160, capture_closed),
        )

    def on_ready(ready: bool) -> None:
        if not ready:
            finish(False)
            return
        view.page().runJavaScript(
            "window.onepicLive2D.setPaused(true);"
            "window.onepicLive2D.setLayeredBlink(false)",
            lambda _result: QTimer.singleShot(300, capture_open),
        )

    view.ready_changed.connect(on_ready)
    QTimer.singleShot(timeout_ms, lambda: finish(False))
    app.exec()
    diagnostics = view.diagnostics
    view.close()
    view.deleteLater()
    app.processEvents()

    blink_pixels = (
        changed_pixels(open_path, closed_path)
        if open_path.is_file() and closed_path.is_file()
        else 0
    )
    motion_pixels = (
        changed_pixels(motion_start_path, motion_end_path)
        if motion_start_path.is_file() and motion_end_path.is_file()
        else 0
    )
    automatic_blink_pixels = (
        changed_pixels(open_path, automatic_blink_path)
        if open_path.is_file() and automatic_blink_path.is_file()
        else 0
    )
    renderer = diagnostics.get("animation_profile", {}).get("renderer")
    passed = bool(
        state.get("success")
        and renderer == "layered-webgl"
        and blink_pixels >= 500
        and motion_pixels >= 500
        and automatic_blink_pixels >= 500
    )
    report = {
        "status": "passed" if passed else "failed",
        "renderer": renderer,
        "blink_changed_pixels": blink_pixels,
        "motion_changed_pixels": motion_pixels,
        "automatic_blink_changed_pixels": automatic_blink_pixels,
        "automatic_blink_status": state.get("automatic_status", {}),
        "timeout_ms": timeout_ms,
        "captures": {
            "eyes_open": str(open_path.resolve()),
            "eyes_closed": str(closed_path.resolve()),
            "motion_start": str(motion_start_path.resolve()),
            "motion_end": str(motion_end_path.resolve()),
            "automatic_blink": str(automatic_blink_path.resolve()),
        },
        "diagnostics": diagnostics,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
