"""
本模块使用真实 PetWindow 与文字气泡控件生成本地互动台词审核图。

职责范围：
- 从指定宠物运行清单加载已经批准并激活的角色素材；
- 读取尚未启用的候选台词配置，不修改运行清单或用户设置；
- 用实际 QLabel 样式渲染气泡，并与真实桌宠窗口合成为联系表；
- 输出纯本地 PNG 预览，供台词语义、字号、换行和视觉位置人工确认。

输入为宠物运行清单、候选台词 JSON、动作名列表和输出路径。输出为 PNG；
本模块不访问网络、不保存用户自定义台词，也不会激活候选配置。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.dialogue import normalize_dialogue_config
from onepic_desktop_pet.window import PetWindow


def _render_scene(
    window: PetWindow,
    *,
    action: str,
    text: str,
    label: str,
    cell_size: tuple[int, int],
) -> QPixmap:
    """把真实桌宠窗口和真实文字气泡控件合成一个审核单元格。"""

    app = QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication 尚未初始化")
    window.dialogue_bubble.hide()
    window._show_dialogue_text(text)
    window.show()
    app.processEvents()

    pet = window.grab()
    bubble = window.dialogue_bubble.grab()
    width, height = cell_size
    scene = QPixmap(width, height)
    scene.fill(QColor(238, 242, 246))
    painter = QPainter(scene)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.fillRect(QRect(0, 0, width, 42), QColor(31, 41, 55))
    painter.setPen(QColor(255, 255, 255))
    painter.setFont(QFont("Microsoft YaHei UI", 11))
    painter.drawText(
        QRect(14, 0, width - 28, 42),
        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        f"{label} · {action}",
    )

    pet_target_height = min(280, height - 112)
    pet_scaled = pet.scaledToHeight(
        pet_target_height,
        Qt.TransformationMode.SmoothTransformation,
    )
    pet_x = (width - pet_scaled.width()) // 2
    pet_y = height - pet_scaled.height() - 12
    painter.drawPixmap(pet_x, pet_y, pet_scaled)

    max_bubble_width = width - 36
    bubble_scaled = bubble
    if bubble.width() > max_bubble_width:
        bubble_scaled = bubble.scaledToWidth(
            max_bubble_width,
            Qt.TransformationMode.SmoothTransformation,
        )
    bubble_x = (width - bubble_scaled.width()) // 2
    bubble_y = max(52, pet_y - bubble_scaled.height() - 8)
    painter.drawPixmap(bubble_x, bubble_y, bubble_scaled)
    painter.end()
    window.dialogue_bubble.hide()
    return scene


def render_preview(
    manifest_path: Path,
    config_path: Path,
    output: Path,
    actions: list[str],
) -> Path:
    """生成两列真实气泡预览联系表并保存。"""

    os.environ["ONEPIC_PET_MANIFEST"] = str(manifest_path.resolve())
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    config = normalize_dialogue_config(raw)
    app = QApplication.instance() or QApplication([])
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = PetWindow(PetSettings(display_height=300))
    window._dialogue_config = config
    window._dialogue_pet_id = str(
        json.loads(manifest_path.read_text(encoding="utf-8")).get(
            "pet_id",
            "preview",
        )
    )
    window._reset_dialogue_selector()

    labels = config["action_labels"]
    lines = config["lines"]
    selected = [name for name in actions if lines.get(name)]
    if not selected:
        raise ValueError("指定动作没有可预览台词")
    columns = 2
    cell_width, cell_height = 430, 400
    rows = (len(selected) + columns - 1) // columns
    sheet = QPixmap(cell_width * columns, cell_height * rows)
    sheet.fill(QColor(222, 228, 235))
    painter = QPainter(sheet)
    for index, action in enumerate(selected):
        cell = _render_scene(
            window,
            action=action,
            text=lines[action][0],
            label=str(labels.get(action, action)),
            cell_size=(cell_width, cell_height),
        )
        painter.drawPixmap(
            (index % columns) * cell_width,
            (index // columns) * cell_height,
            cell,
        )
    painter.end()

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not sheet.save(str(output), "PNG"):
        raise OSError(f"无法保存台词预览：{output}")
    window.close()
    window.deleteLater()
    app.processEvents()
    return output


def parse_args() -> argparse.Namespace:
    """解析台词预览参数。"""

    parser = argparse.ArgumentParser(description="生成真实桌宠文字气泡审核图")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--actions",
        default="wave,typing,wait_response_v1,micro_expressions_v1",
    )
    return parser.parse_args()


def main() -> int:
    """执行预览生成并打印输出路径。"""

    args = parse_args()
    output = render_preview(
        args.manifest,
        args.config,
        args.output,
        [item.strip() for item in args.actions.split(",") if item.strip()],
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
