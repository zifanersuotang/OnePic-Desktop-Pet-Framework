"""
本模块生成桌宠表情符号的静态视觉验收图。

职责范围：
- 固定使用公开演示素材，依次渲染所有带符号增强的互动状态；
- 把渲染结果排列为联系表，便于开源贡献者检查位置、颜色、缩放和透明边缘；
- 默认把结果写入被 Git 忽略的 `user_assets/review/`，不修改角色动作素材。

Agent 快速定位：
- 需要预览的状态位于 PREVIEW_STATES；
- 联系表生成入口位于 render_preview()；
- 命令行入口位于 main()。

输入为可选输出路径，输出为 PNG 预览图。模块不访问网络，也不会读取本地角色原图。

使用示例：
    py tools/render_emotion_preview.py
    py tools/render_emotion_preview.py --output user_assets/review/emotions.png
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ONEPIC_USE_DEMO_ASSETS"] = "1"

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.window import PetWindow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREVIEW_STATES = (
    PetState.HAPPY,
    PetState.SHY,
    PetState.SURPRISED,
    PetState.ANNOYED,
    PetState.SLEEPY,
    PetState.CURIOUS,
    PetState.SELFIE,
    PetState.DRAG,
)


def render_preview(output: Path) -> Path:
    """渲染八种互动状态并保存两行四列的联系表。"""

    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings(display_height=220))
    cell_width, cell_height = 270, 250
    canvas = QPixmap(cell_width * 4, cell_height * 2)
    canvas.fill(QColor(245, 245, 242))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for index, state in enumerate(PREVIEW_STATES):
        window.set_state(state)
        app.processEvents()
        frame = window.label.pixmap()
        column, row = index % 4, index // 4
        left, top = column * cell_width, row * cell_height
        painter.drawPixmap(
            QRect(left + 15, top + 8, cell_width - 30, cell_height - 16),
            frame,
            frame.rect(),
        )
    painter.end()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not canvas.save(str(output), "PNG"):
        raise OSError(f"无法保存表情预览图：{output}")
    window.close()
    window.deleteLater()
    app.processEvents()
    return output


def parse_args() -> argparse.Namespace:
    """解析可选输出路径。"""

    parser = argparse.ArgumentParser(description="生成桌宠表情符号视觉验收图")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "user_assets" / "review" / "emotion-symbol-preview.png",
    )
    return parser.parse_args()


def main() -> int:
    """执行预览图渲染并打印结果路径。"""

    output = render_preview(parse_args().output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
