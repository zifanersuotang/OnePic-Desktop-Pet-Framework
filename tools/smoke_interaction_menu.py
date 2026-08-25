"""用真实 Qt 菜单点击验证互动动作无重复、无空项且指向正确执行目标。

脚本从指定运行清单独立推导期望菜单，逐项弹出 ``互动动作`` 子菜单并通过
``QTest.mouseClick`` 点击。扩展动作必须进入对应序列，基础动作必须进入对应状态；
报告记录实际 pet-id，并与菜单截图共同用于发布审计；不访问网络，也不修改用户设置。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.window import PetWindow


def _file_sha256(path: Path) -> str:
    """计算被测 runtime manifest 的 SHA-256。"""

    return hashlib.sha256(path.read_bytes()).hexdigest()


MANUAL_STATE_NAMES = {
    PetState.WAVE.value,
    PetState.HAPPY.value,
    PetState.SHY.value,
    PetState.SURPRISED.value,
    PetState.ANNOYED.value,
    PetState.SLEEPY.value,
    PetState.CURIOUS.value,
}


def parse_args() -> argparse.Namespace:
    """读取实际运行清单、报告和菜单截图路径。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--display-height", type=int, default=420)
    return parser.parse_args()


def expected_interaction_actions(
    manifest: dict[str, object],
) -> list[dict[str, str]]:
    """按公开优先级从清单推导去重后的互动菜单动作。"""

    results = [
        {
            "label": "打个招呼",
            "target": "builtin:interaction",
            "source": "builtin",
        },
        {
            "label": "自拍一下",
            "target": "builtin:selfie",
            "source": "builtin",
        },
    ]
    shown_labels = {item["label"] for item in results}
    shown_targets = {item["target"] for item in results}
    extensions = manifest.get("extension_animations", {})
    if not isinstance(extensions, dict):
        extensions = {}
    companion = manifest.get("companion_behaviors", {})
    if not isinstance(companion, dict):
        companion = {}

    def add(label: object, target: str, source: str) -> None:
        normalized_label = str(label).strip() if label else ""
        if (
            not normalized_label
            or normalized_label in shown_labels
            or target in shown_targets
        ):
            return
        results.append(
            {
                "label": normalized_label,
                "target": target,
                "source": source,
            }
        )
        shown_labels.add(normalized_label)
        shown_targets.add(target)

    signature = companion.get("signature_action", {})
    if isinstance(signature, dict) and bool(signature.get("enabled", False)):
        sequence = str(signature.get("sequence", "")).strip()
        state = str(signature.get("state", "wave")).strip().lower()
        target = (
            f"extension:{sequence}"
            if sequence in extensions
            else f"state:{state if state in MANUAL_STATE_NAMES else 'wave'}"
        )
        add(signature.get("label") or "招牌动作", target, "signature")

    for name, raw_config in extensions.items():
        if not isinstance(raw_config, dict):
            continue
        add(raw_config.get("menu_label"), f"extension:{name}", "extension")

    manual_actions = companion.get("manual_actions", [])
    if isinstance(manual_actions, list):
        for raw_action in manual_actions:
            if not isinstance(raw_action, dict):
                continue
            sequence = str(raw_action.get("sequence", "")).strip()
            state = str(raw_action.get("state", "")).strip().lower()
            if sequence in extensions:
                target = f"extension:{sequence}"
            elif state in MANUAL_STATE_NAMES:
                target = f"state:{state}"
            else:
                continue
            add(raw_action.get("label"), target, "manual")
    return results


def interaction_menu(window: PetWindow) -> tuple[QMenu, QMenu]:
    """构建右键菜单并返回父菜单与互动子菜单。"""

    menu = window._build_context_menu()
    submenu = next(
        (
            action.menu()
            for action in menu.actions()
            if action.text() == "互动动作"
        ),
        None,
    )
    if submenu is None:
        menu.deleteLater()
        raise RuntimeError("右键菜单缺少“互动动作”子菜单")
    return menu, submenu


def reset_action_state(window: PetWindow) -> None:
    """在两次点击间恢复待机，避免上一动作阻挡下一动作。"""

    window._clear_extension_animation()
    window.interaction_timer.stop()
    window.state_timer.stop()
    window.photo_timer.stop()
    window.photo_bubble.hide()
    window.set_state(PetState.IDLE)


def click_menu_action(
    app: QApplication,
    window: PetWindow,
    label: str,
    popup_point: QPoint,
    *,
    capture: Path | None = None,
) -> list[str]:
    """弹出真实子菜单，按动作几何中心点击指定标签并返回当次标签序列。"""

    menu, submenu = interaction_menu(window)
    labels = [action.text() for action in submenu.actions()]
    action = next((item for item in submenu.actions() if item.text() == label), None)
    if action is None:
        menu.deleteLater()
        raise RuntimeError(f"互动菜单缺少动作：{label}")
    submenu.popup(popup_point)
    app.processEvents()
    QTest.qWait(80)
    if capture is not None and not submenu.grab().save(str(capture), "PNG"):
        submenu.close()
        menu.deleteLater()
        raise RuntimeError(f"无法保存菜单截图：{capture}")
    geometry = submenu.actionGeometry(action)
    if geometry.isEmpty():
        submenu.close()
        menu.deleteLater()
        raise RuntimeError(f"菜单动作没有可点击区域：{label}")
    QTest.mouseClick(
        submenu,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        geometry.center(),
        5,
    )
    app.processEvents()
    QTest.qWait(15)
    menu.deleteLater()
    return labels


def action_result(window: PetWindow, item: dict[str, str]) -> dict[str, object]:
    """把一次点击后的窗口状态归一化为可审计结果。"""

    target = item["target"]
    if target == "builtin:interaction":
        passed = window.state.value in MANUAL_STATE_NAMES
    elif target == "builtin:selfie":
        passed = window.state is PetState.SELFIE
    elif target.startswith("extension:"):
        passed = window._extension_active_name == target.removeprefix("extension:")
    elif target.startswith("state:"):
        passed = window.state.value == target.removeprefix("state:")
    else:
        passed = False
    return {
        **item,
        "passed": passed,
        "active_extension": window._extension_active_name,
        "state": window.state.value,
    }


def main() -> int:
    """执行真实菜单点击烟测并输出 JSON 报告。"""

    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    os.environ["ONEPIC_PET_MANIFEST"] = str(manifest_path)
    os.environ.pop("ONEPIC_USE_DEMO_ASSETS", None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.capture.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)
    window = PetWindow(PetSettings(display_height=args.display_height))
    window.move(24, 240)
    window.show()
    app.processEvents()
    QTest.qWait(180)
    expected = expected_interaction_actions(manifest)
    expected_labels = [item["label"] for item in expected]
    screen = QApplication.primaryScreen()
    popup_point = QPoint(80, 80)
    if screen is not None:
        popup_point = screen.availableGeometry().topLeft() + QPoint(80, 80)

    click_results: list[dict[str, object]] = []
    actual_labels: list[str] = []
    error: str | None = None
    try:
        for index, item in enumerate(expected):
            reset_action_state(window)
            labels = click_menu_action(
                app,
                window,
                item["label"],
                popup_point,
                capture=args.capture if index == 0 else None,
            )
            if not actual_labels:
                actual_labels = labels
            elif labels != actual_labels:
                raise RuntimeError("互动菜单在逐项点击期间发生了非预期变化")
            click_results.append(action_result(window, item))
    except Exception as exc:  # pragma: no cover - 主函数负责落盘诊断
        error = f"{type(exc).__name__}: {exc}"

    labels_unique = len(actual_labels) == len(set(actual_labels))
    targets_unique = len(expected) == len({item["target"] for item in expected})
    passed = bool(
        error is None
        and actual_labels == expected_labels
        and labels_unique
        and targets_unique
        and len(click_results) == len(expected)
        and all(bool(item.get("passed")) for item in click_results)
        and args.capture.is_file()
    )
    report = {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "pet_id": manifest.get("pet_id"),
        "build_id": manifest.get("build_id"),
        "runtime_manifest_sha256": _file_sha256(manifest_path),
        "manifest": str(manifest_path),
        "display_height": args.display_height,
        "capture": str(args.capture.resolve()),
        "expected_labels": expected_labels,
        "actual_labels": actual_labels,
        "labels_unique": labels_unique,
        "targets_unique": targets_unique,
        "click_results": click_results,
        "error": error,
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
