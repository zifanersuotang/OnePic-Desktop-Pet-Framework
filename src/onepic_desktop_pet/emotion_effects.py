"""
本模块提供与角色素材解耦的表情符号绘制能力。

职责范围：
- 将桌宠行为状态映射为闪光、爱心、惊叹号、疑问号、怒气、困倦和汗滴等视觉提示；
- 使用 PySide6 的矢量绘制接口把符号叠加到当前动画帧；
- 保持符号可随桌宠尺寸和高 DPI 比例缩放，不依赖某一套人物图片或外部字体文件。

Agent 快速定位：
- 状态与效果映射位于 emotion_effect_name()；
- 统一绘制入口位于 draw_emotion_effect()；
- 新增效果时应同时补充窗口测试，避免只修改演示角色素材。

输入为目标 QPixmap、PetState 和动画相位，输出为带有表情提示的 QPixmap 副本。
本模块不读取或写入文件、不访问网络，也不修改传入的原始素材。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

from .behavior import PetState


_EFFECTS: dict[PetState, str] = {
    PetState.WAVE: "sparkle",
    PetState.HAPPY: "sparkle",
    PetState.SHY: "heart",
    PetState.SURPRISED: "exclamation",
    PetState.ANNOYED: "anger",
    PetState.SLEEPY: "sleep",
    PetState.CURIOUS: "question",
    PetState.SELFIE: "flash",
    PetState.DRAG: "sweat",
}


def emotion_effect_name(state: PetState) -> str | None:
    """返回行为状态对应的表情增强效果名称。"""

    return _EFFECTS.get(state)


def _heart_path(center: QPointF, size: float) -> QPainterPath:
    """构造一个适合小尺寸显示的爱心路径。"""

    path = QPainterPath()
    path.moveTo(center.x(), center.y() + size * 0.45)
    path.cubicTo(
        center.x() - size * 0.85,
        center.y() - size * 0.05,
        center.x() - size * 0.48,
        center.y() - size * 0.72,
        center.x(),
        center.y() - size * 0.28,
    )
    path.cubicTo(
        center.x() + size * 0.48,
        center.y() - size * 0.72,
        center.x() + size * 0.85,
        center.y() - size * 0.05,
        center.x(),
        center.y() + size * 0.45,
    )
    return path


def _draw_sparkle(painter: QPainter, center: QPointF, size: float) -> None:
    """绘制四向闪光和两枚小亮点。"""

    color = QColor(255, 190, 55, 245)
    painter.setPen(QPen(color, max(2.0, size * 0.16), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(center.x(), center.y() - size), QPointF(center.x(), center.y() + size))
    painter.drawLine(QPointF(center.x() - size, center.y()), QPointF(center.x() + size, center.y()))
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(center.x() + size * 1.35, center.y() - size * 0.75), size * 0.20, size * 0.20)
    painter.drawEllipse(QPointF(center.x() - size * 1.15, center.y() + size * 0.85), size * 0.14, size * 0.14)


def _draw_exclamation(painter: QPainter, center: QPointF, size: float) -> None:
    """不用字体绘制带白边的惊叹号。"""

    start = QPointF(center.x(), center.y() - size * 1.15)
    end = QPointF(center.x(), center.y() + size * 0.35)
    for color, width in (
        (QColor(255, 255, 255, 235), size * 0.62),
        (QColor(255, 178, 45, 255), size * 0.38),
    ):
        painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(start, end)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(center.x(), center.y() + size * 0.95), width * 0.48, width * 0.48)


def _draw_question(painter: QPainter, center: QPointF, size: float) -> None:
    """不用字体绘制带白边的疑问号曲线。"""

    path = QPainterPath(QPointF(center.x() - size * 0.65, center.y() - size * 0.60))
    path.cubicTo(
        center.x() - size * 0.55,
        center.y() - size * 1.45,
        center.x() + size * 0.85,
        center.y() - size * 1.35,
        center.x() + size * 0.72,
        center.y() - size * 0.48,
    )
    path.cubicTo(
        center.x() + size * 0.62,
        center.y() + size * 0.05,
        center.x(),
        center.y() + size * 0.02,
        center.x(),
        center.y() + size * 0.48,
    )
    for color, width in (
        (QColor(255, 255, 255, 235), size * 0.50),
        (QColor(135, 115, 220, 255), size * 0.28),
    ):
        painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(center.x(), center.y() + size * 1.02), width * 0.46, width * 0.46)


def _draw_sleep(painter: QPainter, center: QPointF, size: float) -> None:
    """用三枚递减的折线 Z 表示困倦，避免依赖字体。"""

    color = QColor(75, 145, 225, 255)
    painter.setPen(QPen(color, max(2.0, size * 0.20), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    for index, scale in enumerate((0.78, 0.56, 0.38)):
        x = center.x() + index * size * 0.72
        y = center.y() - index * size * 0.62
        span = size * scale
        painter.drawLine(QPointF(x - span * 0.45, y - span * 0.45), QPointF(x + span * 0.45, y - span * 0.45))
        painter.drawLine(QPointF(x + span * 0.45, y - span * 0.45), QPointF(x - span * 0.45, y + span * 0.45))
        painter.drawLine(QPointF(x - span * 0.45, y + span * 0.45), QPointF(x + span * 0.45, y + span * 0.45))


def draw_emotion_effect(
    pixmap: QPixmap,
    state: PetState,
    phase: int = 0,
) -> QPixmap:
    """在图片副本的头部右上方绘制当前状态的动态表情符号。"""

    effect = emotion_effect_name(state)
    if effect is None or pixmap.isNull():
        return pixmap
    output = QPixmap(pixmap)
    ratio = max(1.0, output.devicePixelRatio())
    width = output.width() / ratio
    height = output.height() / ratio
    size = max(10.0, min(width, height) * 0.075)
    float_y = math.sin((phase % 12) * math.pi / 6.0) * size * 0.12
    center = QPointF(width * 0.72, height * 0.20 + float_y)

    painter = QPainter(output)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if effect == "sparkle":
        _draw_sparkle(painter, center, size * 0.75)
    elif effect == "heart":
        painter.setPen(QPen(QColor(255, 255, 255, 225), max(2.0, size * 0.14)))
        painter.setBrush(QColor(255, 105, 150, 245))
        painter.drawPath(_heart_path(center, size * 0.72))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 165, 195, 235))
        painter.drawPath(
            _heart_path(
                QPointF(center.x() + size * 1.05, center.y() - size * 0.55),
                size * 0.38,
            )
        )
    elif effect == "exclamation":
        _draw_exclamation(painter, center, size)
    elif effect == "question":
        _draw_question(painter, center, size)
    elif effect == "sleep":
        _draw_sleep(painter, center, size)
    elif effect == "anger":
        color = QColor(235, 72, 60, 255)
        pen = QPen(color, max(2.5, size * 0.22), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        x, y = center.x(), center.y()
        painter.drawLine(QPointF(x - size, y - size * 0.25), QPointF(x - size * 0.25, y - size * 0.25))
        painter.drawLine(QPointF(x - size * 0.25, y - size), QPointF(x - size * 0.25, y - size * 0.25))
        painter.drawLine(QPointF(x + size, y + size * 0.25), QPointF(x + size * 0.25, y + size * 0.25))
        painter.drawLine(QPointF(x + size * 0.25, y + size), QPointF(x + size * 0.25, y + size * 0.25))
    elif effect == "flash":
        _draw_sparkle(painter, QPointF(width * 0.34, height * 0.56), size * 0.88)
    elif effect == "sweat":
        painter.setPen(QPen(QColor(255, 255, 255, 220), max(1.5, size * 0.10)))
        painter.setBrush(QColor(80, 170, 235, 240))
        drop = QPainterPath()
        drop.moveTo(center.x(), center.y() - size)
        drop.cubicTo(
            center.x() - size * 0.85,
            center.y(),
            center.x() - size * 0.55,
            center.y() + size * 0.85,
            center.x(),
            center.y() + size * 0.85,
        )
        drop.cubicTo(
            center.x() + size * 0.55,
            center.y() + size * 0.85,
            center.x() + size * 0.85,
            center.y(),
            center.x(),
            center.y() - size,
        )
        painter.drawPath(drop)
    painter.end()
    return output
