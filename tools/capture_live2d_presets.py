"""单次逐项加载 Cubism 模型，捕获带固定外观与常驻配件的关键姿态。

对声明为 ``pin_to=nose`` 的配件，同时验证其中心与本帧 Cubism 鼻部网格中心
误差不超过 1 CSS 像素，防止动态跟踪正常但中性锚点仍有固定偏移。
若提供运行清单，还会逐帧验证水印/发型等固定参数未被表情或动作重置。
中性姿态还会检查左右猪耳相对鼻中轴的对称误差，避免整组配件横向偏斜。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.live2d_view import Live2DIdleView
from onepic_desktop_pet.resources import resource_path


NOSE_ALIGNMENT_THRESHOLD_CSS_PX = 1.0
EAR_PAIR_CENTER_THRESHOLD_CSS_PX = 0.75


def sha256(path: Path) -> str:
    """返回文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nose_alignment_error_css_px(status: object) -> float | None:
    """返回鼻部配件中心误差；没有鼻部绑定时返回 None。"""

    if not isinstance(status, dict):
        return None
    accessories = status.get("accessories")
    if not isinstance(accessories, dict):
        return None
    layers = accessories.get("layers")
    binding = accessories.get("binding")
    if not isinstance(layers, list) or not isinstance(binding, dict):
        return None
    nose_layer = next(
        (
            layer
            for layer in layers
            if isinstance(layer, dict) and layer.get("pinTo") == "nose"
        ),
        None,
    )
    if nose_layer is None:
        return None
    bounds = nose_layer.get("bounds")
    current_nose = binding.get("currentNose")
    if not isinstance(bounds, dict) or not isinstance(current_nose, dict):
        return math.inf
    try:
        layer_center_x = float(bounds["x"]) + float(bounds["width"]) / 2
        layer_center_y = float(bounds["y"]) + float(bounds["height"]) / 2
        delta_x = layer_center_x - float(current_nose["x"])
        delta_y = layer_center_y - float(current_nose["y"])
    except (KeyError, TypeError, ValueError):
        return math.inf
    return math.hypot(delta_x, delta_y)


def ear_pair_symmetry_metrics(status: object) -> dict[str, float] | None:
    """返回左右猪耳相对实时鼻中轴的中性对称指标。"""

    if not isinstance(status, dict):
        return None
    accessories = status.get("accessories")
    if not isinstance(accessories, dict):
        return None
    layers = accessories.get("layers")
    binding = accessories.get("binding")
    if not isinstance(layers, list) or not isinstance(binding, dict):
        return None
    role_layers = {
        str(layer.get("role")): layer
        for layer in layers
        if isinstance(layer, dict)
        and layer.get("role") in {"pig_ear_left", "pig_ear_right"}
    }
    if not role_layers:
        return None
    if set(role_layers) != {"pig_ear_left", "pig_ear_right"}:
        return {
            "pair_midpoint_offset_css_px": math.inf,
            "pair_midpoint_error_css_px": math.inf,
            "center_y_difference_css_px": math.inf,
            "width_difference_css_px": math.inf,
            "height_difference_css_px": math.inf,
        }
    current_nose = binding.get("currentNose")
    if not isinstance(current_nose, dict):
        return {
            "pair_midpoint_offset_css_px": math.inf,
            "pair_midpoint_error_css_px": math.inf,
            "center_y_difference_css_px": math.inf,
            "width_difference_css_px": math.inf,
            "height_difference_css_px": math.inf,
        }

    def geometry(layer: dict[str, object]) -> tuple[float, float, float, float]:
        bounds = layer.get("bounds")
        if not isinstance(bounds, dict):
            raise ValueError("missing-bounds")
        width = float(bounds["width"])
        height = float(bounds["height"])
        return (
            float(bounds["x"]) + width / 2,
            float(bounds["y"]) + height / 2,
            width,
            height,
        )

    try:
        left_x, left_y, left_width, left_height = geometry(
            role_layers["pig_ear_left"]
        )
        right_x, right_y, right_width, right_height = geometry(
            role_layers["pig_ear_right"]
        )
        nose_x = float(current_nose["x"])
    except (KeyError, TypeError, ValueError):
        return {
            "pair_midpoint_offset_css_px": math.inf,
            "pair_midpoint_error_css_px": math.inf,
            "center_y_difference_css_px": math.inf,
            "width_difference_css_px": math.inf,
            "height_difference_css_px": math.inf,
        }
    midpoint_offset = (left_x + right_x) / 2 - nose_x
    return {
        "pair_midpoint_offset_css_px": midpoint_offset,
        "pair_midpoint_error_css_px": abs(midpoint_offset),
        "center_y_difference_css_px": abs(left_y - right_y),
        "width_difference_css_px": abs(left_width - right_width),
        "height_difference_css_px": abs(left_height - right_height),
    }


def parse_args() -> argparse.Namespace:
    """读取命令行参数。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--accessory-manifest", required=True)
    parser.add_argument("--preset-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--load-timeout-ms", type=int, default=30_000)
    parser.add_argument("--view-width", type=int, default=360)
    parser.add_argument("--view-height", type=int, default=420)
    parser.add_argument(
        "--display-scale",
        type=float,
        default=1.45,
        help="Live2D 在视口内的显示缩放，默认 1.45",
    )
    return parser.parse_args()


def _contact_sheet(
    captures: list[dict[str, Any]], output: Path
) -> None:
    """生成预设联系表，方便一次性检查身份、表情与挂件。"""

    tile_width, tile_height = 300, 430
    columns = 4
    rows = (len(captures) + columns - 1) // columns
    sheet = Image.new(
        "RGBA",
        (columns * tile_width, rows * tile_height),
        (34, 30, 40, 255),
    )
    draw = ImageDraw.Draw(sheet)
    for index, item in enumerate(captures):
        with Image.open(item["path"]).convert("RGBA") as image:
            bbox = image.getchannel("A").getbbox()
            if bbox is None:
                continue
            subject = image.crop(bbox)
            scale = min(270 / subject.width, 382 / subject.height)
            subject = subject.resize(
                (
                    max(1, round(subject.width * scale)),
                    max(1, round(subject.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        column, row = index % columns, index // columns
        x = column * tile_width + (tile_width - subject.width) // 2
        y = row * tile_height + 30 + (382 - subject.height)
        sheet.alpha_composite(subject, (x, y))
        draw.text(
            (column * tile_width + 10, row * tile_height + 8),
            str(item["name"]),
            fill=(250, 235, 245, 255),
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, "PNG", optimize=True)


def main() -> int:
    """按预设逐项捕获，避免表达式叠加残留并输出哈希报告。"""

    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = args.runtime_root.resolve()
    preset_path = args.preset_file.resolve()
    output_dir = args.output_dir.resolve()
    if not root.is_dir() or not preset_path.is_file():
        raise FileNotFoundError("Live2D 运行目录或预设文件不存在")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"输出目录非空，拒绝覆盖：{output_dir}")
    if not 0.35 <= args.display_scale <= 2.5:
        raise ValueError("display-scale 必须在 0.35–2.5")
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_manifest_path: Path | None = None
    persistent_parameters: dict[str, float] = {}
    if args.runtime_manifest is not None:
        runtime_manifest_path = args.runtime_manifest.resolve()
        if (
            not runtime_manifest_path.is_file()
            or runtime_manifest_path.parent != root
        ):
            raise FileNotFoundError(
                "运行清单必须位于 runtime-root 根目录且真实存在"
            )
        runtime_manifest = json.loads(
            runtime_manifest_path.read_text(encoding="utf-8-sig")
        )
        raw_persistent = runtime_manifest.get("hybrid_runtime", {}).get(
            "persistent_parameters", {}
        )
        if not isinstance(raw_persistent, dict) or any(
            not isinstance(parameter_id, str)
            or not parameter_id
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for parameter_id, value in raw_persistent.items()
        ):
            raise ValueError("运行清单中的 Live2D 固定参数无效")
        persistent_parameters = {
            parameter_id: float(value)
            for parameter_id, value in raw_persistent.items()
        }
    model_path = (root / args.model).resolve()
    accessory_manifest_path = (root / args.accessory_manifest).resolve()
    if (
        root not in model_path.parents
        or root not in accessory_manifest_path.parents
        or not model_path.is_file()
        or not accessory_manifest_path.is_file()
    ):
        raise FileNotFoundError("Live2D 模型或配件清单缺失、越界")
    model_document = json.loads(model_path.read_text(encoding="utf-8-sig"))
    moc_value = str(
        model_document.get("FileReferences", {}).get("Moc", "")
    ).strip()
    moc_path = (model_path.parent / moc_value).resolve()
    if not moc_value or root not in moc_path.parents or not moc_path.is_file():
        raise FileNotFoundError("Live2D 模型引用的 moc3 缺失、越界")
    web_bundle_path = resource_path(
        "assets/live2d_web/live2d.bundle.js"
    ).resolve()
    if not web_bundle_path.is_file():
        raise FileNotFoundError("缺少当前 Live2D Web 运行时 bundle")
    preset_document = json.loads(preset_path.read_text(encoding="utf-8-sig"))
    presets = preset_document.get("presets")
    if preset_document.get("schema_version") != 1 or not isinstance(
        presets, dict
    ) or not presets:
        raise ValueError("预设文件结构无效")
    settle_ms = int(preset_document.get("settle_ms", 1250))
    if not 100 <= settle_ms <= 5000:
        raise ValueError("settle_ms 必须在 100–5000")

    QCoreApplication.setAttribute(
        Qt.ApplicationAttribute.AA_ShareOpenGLContexts
    )
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    items = list(presets.items())
    captures: list[dict[str, Any]] = []
    state: dict[str, Any] = {
        "index": 0,
        "view": None,
        "finished": False,
        "error": None,
    }

    idle_animation = {
        "enabled": False,
        "intensity": 0.72,
        "display_scale": args.display_scale,
        "display_offset_y_ratio": 0.06,
        "blink": False,
        "eye_motion": False,
        "head_motion": False,
        "body_motion": False,
        "hair_motion": False,
        "root_motion": False,
        "blink_parameter_mode": "smooth",
        "blink_interval_ms": [3200, 5200],
        "blink_duration_ms": [130, 80, 190],
    }

    def fail(message: str) -> None:
        state["error"] = message
        state["finished"] = True
        app.quit()

    def start_next() -> None:
        if state["index"] >= len(items):
            state["finished"] = True
            app.quit()
            return
        name, preset = items[state["index"]]
        if not isinstance(preset, dict):
            fail(f"预设 {name} 必须是对象")
            return
        expression = preset.get("expression")
        parameters = preset.get("parameters", {})
        if expression is not None and not isinstance(expression, str):
            fail(f"预设 {name}.expression 必须是字符串或 null")
            return
        if not isinstance(parameters, dict) or not all(
            isinstance(key, str) and isinstance(value, (int, float))
            for key, value in parameters.items()
        ):
            fail(f"预设 {name}.parameters 必须是数值对象")
            return
        config: dict[str, object] = {
            "model": args.model,
            "accessory_manifest": args.accessory_manifest,
            "load_timeout_ms": args.load_timeout_ms,
            "idle_animation": idle_animation,
            "persistent_parameters": persistent_parameters,
        }
        view = Live2DIdleView(root / "runtime-manifest.json", config)
        state["view"] = view
        view.resize(args.view_width, args.view_height)
        view.show()
        timed_out = {"value": False}

        def on_timeout() -> None:
            if state.get("view") is view and not view.is_ready:
                timed_out["value"] = True
                fail(f"预设 {name} 的 Live2D 加载超时：{view.diagnostics}")

        QTimer.singleShot(args.load_timeout_ms + 8_000, on_timeout)

        def on_ready(ready: bool) -> None:
            if timed_out["value"] or state.get("view") is not view:
                return
            if not ready:
                fail(f"预设 {name} 加载失败：{view.diagnostics}")
                return
            payload = json.dumps(
                {"expression": expression, "parameters": parameters},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            script = (
                "(() => {"
                f"const preset={payload};"
                "const api=window.onepicLive2D;"
                "if(!api){return {ok:false,error:'missing-api'};}"
                "api.setPaused(true);"
                "if(preset.expression && !api.setExpression(preset.expression)){"
                "return {ok:false,error:'unknown-expression'};}"
                "api.setParameters(preset.parameters || {});"
                "api.renderNow();"
                "const status=api.getStatus();"
                "delete status.catalog;"
                "return JSON.stringify({ok:true,status});"
                "})()"
            )

            def after_apply(result: object) -> None:
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except json.JSONDecodeError:
                        pass
                if not isinstance(result, dict) or not result.get("ok"):
                    fail(f"预设 {name} 应用失败：{result!r}")
                    return
                def after_settle(result: object) -> None:
                    if isinstance(result, str):
                        try:
                            result = json.loads(result)
                        except json.JSONDecodeError:
                            pass
                    if not isinstance(result, dict) or not result.get("ok"):
                        fail(f"预设 {name} 稳定帧读取失败：{result!r}")
                        return
                    runtime_status = result.get("status", {})
                    persistent_values = runtime_status.get(
                        "persistentParameterValues", {}
                    )
                    if not isinstance(persistent_values, dict) or any(
                        parameter_id not in persistent_values
                        or not math.isclose(
                            float(persistent_values[parameter_id]),
                            expected,
                            rel_tol=0,
                            abs_tol=1e-5,
                        )
                        for parameter_id, expected in persistent_parameters.items()
                    ):
                        fail(
                            f"预设 {name} 的固定外观参数被模型更新覆盖："
                            f"{persistent_values!r}"
                        )
                        return
                    nose_error = nose_alignment_error_css_px(runtime_status)
                    if nose_error is not None and (
                        not math.isfinite(nose_error)
                        or nose_error > NOSE_ALIGNMENT_THRESHOLD_CSS_PX
                    ):
                        fail(
                            f"预设 {name} 的猪鼻中心偏离 Part56："
                            f"{nose_error:.3f} CSS px > "
                            f"{NOSE_ALIGNMENT_THRESHOLD_CSS_PX:.1f} CSS px"
                        )
                        return
                    ear_symmetry = ear_pair_symmetry_metrics(runtime_status)
                    if name == "neutral" and ear_symmetry is not None:
                        midpoint_error = ear_symmetry[
                            "pair_midpoint_error_css_px"
                        ]
                        if (
                            not math.isfinite(midpoint_error)
                            or midpoint_error
                            > EAR_PAIR_CENTER_THRESHOLD_CSS_PX
                        ):
                            signed_offset = ear_symmetry[
                                "pair_midpoint_offset_css_px"
                            ]
                            fail(
                                "中性姿态的左右猪耳整体偏离鼻部中轴："
                                f"signed={signed_offset:.3f}, "
                                f"abs={midpoint_error:.3f} CSS px > "
                                f"{EAR_PAIR_CENTER_THRESHOLD_CSS_PX:.1f} "
                                "CSS px"
                            )
                            return
                    output = output_dir / f"{name}.png"
                    saved = view.grab().save(str(output), "PNG")
                    if not saved:
                        fail(f"预设 {name} 截图保存失败")
                        return
                    with Image.open(output).convert("RGBA") as image:
                        bbox = image.getchannel("A").getbbox()
                        if bbox is None:
                            fail(f"预设 {name} 截图完全透明")
                            return
                        captures.append(
                            {
                                "name": name,
                                "path": str(output),
                                "sha256": sha256(output),
                                "pixel_size": list(image.size),
                                "alpha_bbox": list(bbox),
                                "expression": expression,
                                "parameters": parameters,
                                "nose_alignment_error_css_px": nose_error,
                                "ear_pair_symmetry": ear_symmetry,
                                "runtime_status": runtime_status,
                            }
                        )
                    view.close()
                    view.deleteLater()
                    state["view"] = None
                    state["index"] += 1
                    # 4K 六纹理模型连续冷启动时，给 Qt WebEngine 足够时间释放
                    # 上一页 WebGL 上下文，避免第三次以后卡在附件加载阶段。
                    QTimer.singleShot(800, start_next)

                settled_status_script = (
                    "(() => {"
                    "const api=window.onepicLive2D;"
                    "if(!api){return JSON.stringify({ok:false,error:'missing-api'});}"
                    "api.renderNow();"
                    "const status=api.getStatus();"
                    "delete status.catalog;"
                    "return JSON.stringify({ok:true,status});"
                    "})()"
                )
                QTimer.singleShot(
                    settle_ms,
                    lambda: view.page().runJavaScript(
                        settled_status_script,
                        after_settle,
                    ),
                )

            view.page().runJavaScript(script, after_apply)

        view.ready_changed.connect(on_ready)

    QTimer.singleShot(0, start_next)
    app.exec()
    if state["error"]:
        print(str(state["error"]), file=sys.stderr)
        return 1
    if not state["finished"] or len(captures) != len(items):
        print("预设捕获未完整结束", file=sys.stderr)
        return 1

    contact_sheet = output_dir / "capture-presets-contact-sheet.png"
    _contact_sheet(captures, contact_sheet)
    nose_errors = [
        float(capture["nose_alignment_error_css_px"])
        for capture in captures
        if capture.get("nose_alignment_error_css_px") is not None
    ]
    neutral_capture = next(
        (capture for capture in captures if capture.get("name") == "neutral"),
        None,
    )
    report = {
        "schema_version": 1,
        "model": args.model,
        "model_sha256": sha256(model_path),
        "moc_sha256": sha256(moc_path),
        "accessory_manifest": args.accessory_manifest,
        "accessory_manifest_sha256": sha256(accessory_manifest_path),
        "runtime_manifest": (
            str(runtime_manifest_path) if runtime_manifest_path else None
        ),
        "runtime_manifest_sha256": (
            sha256(runtime_manifest_path) if runtime_manifest_path else None
        ),
        "persistent_parameters": persistent_parameters,
        "display_scale": args.display_scale,
        "live2d_web_bundle_sha256": sha256(web_bundle_path),
        "preset_file": str(preset_path),
        "preset_file_sha256": sha256(preset_path),
        "capture_count": len(captures),
        "contact_sheet": contact_sheet.name,
        "contact_sheet_sha256": sha256(contact_sheet),
        "nose_alignment_threshold_css_px": NOSE_ALIGNMENT_THRESHOLD_CSS_PX,
        "ear_pair_center_threshold_css_px": (
            EAR_PAIR_CENTER_THRESHOLD_CSS_PX
        ),
        "neutral_ear_pair_symmetry": (
            neutral_capture.get("ear_pair_symmetry")
            if isinstance(neutral_capture, dict)
            else None
        ),
        "maximum_nose_alignment_error_css_px": (
            max(nose_errors) if nose_errors else None
        ),
        "captures": captures,
    }
    (output_dir / "capture-presets-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
