"""标注图元体系：六类工具对应的 QGraphicsItem。

设计原则：图元即对象——画完仍可撤销(从场景移除)、可导出(Scene 渲染)。
全部以「拖拽创建」为交互范式：按下=起点，松开=终点。
"""

import math

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractGraphicsShapeItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsTextItem,
)

DEFAULT_COLOR = QColor("#ef4444")  # iShot 风格默认红
DEFAULT_WIDTH = 3
MOSAIC_BLOCK = 14  # 马赛克像素块大小(逻辑px)


def arrow_head_points(tip: QPointF, tail: QPointF, size: float = 16.0) -> list[QPointF]:
    """箭头头部三角：以 tip 为顶点、沿 tail→tip 方向展开的两翼点。

    纯函数便于单测：返回 [tip, wing_left, wing_right]。
    """
    angle = math.atan2(tip.y() - tail.y(), tip.x() - tail.x())
    spread = math.radians(26)
    return [
        tip,
        QPointF(tip.x() - size * math.cos(angle - spread),
                tip.y() - size * math.sin(angle - spread)),
        QPointF(tip.x() - size * math.cos(angle + spread),
                tip.y() - size * math.sin(angle + spread)),
    ]


class ShapeItem(QAbstractGraphicsShapeItem):
    """矩形/椭圆共基：拖拽矩形定义几何，笔刷由颜色+线宽决定。

    起点必须独立保存：QRectF(p, p) 的 isNull() 恒为真，
    若从 rect 反推起点会在拖拽中丢失锚点（v1 初版的真 bug）。
    """

    def __init__(self, start: QPointF, end: QPointF, color: QColor,
                 width: int = DEFAULT_WIDTH, filled: bool = False):
        super().__init__()
        self._start = start
        self._rect = QRectF(start, end).normalized()
        pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        # 半透明填充：既标注又能看到底图
        fill = QColor(color) if filled else QColor(color)
        fill.setAlpha(60 if filled else 0)
        self.setBrush(QBrush(fill))

    def update_end(self, end: QPointF) -> None:
        self.prepareGeometryChange()
        self._rect = QRectF(self._start, end).normalized()

    def boundingRect(self) -> QRectF:
        w = self.pen().widthF() + 2
        return self._rect.adjusted(-w, -w, w, w)


class RectItem(ShapeItem):
    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawRect(self._rect)


class EllipseItem(ShapeItem):
    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawEllipse(self._rect)


class ArrowItem(QAbstractGraphicsShapeItem):
    """箭头 = 线段 + 三角头。拖拽方向即箭头方向（后点为头）。"""

    def __init__(self, start: QPointF, end: QPointF, color: QColor,
                 width: int = DEFAULT_WIDTH):
        super().__init__()
        self._line = QLineF(start, end)
        pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        self.setPen(pen)
        self.setBrush(QBrush(color))

    def update_end(self, end: QPointF) -> None:
        self.prepareGeometryChange()
        self._line.setP2(end)

    def boundingRect(self) -> QRectF:
        w = self.pen().widthF() + 18  # 计入箭头头部尺寸
        r = QRectF(self._line.p1(), self._line.p2()).normalized()
        return r.adjusted(-w, -w, w, w)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawLine(self._line)
        head = QPolygonF(arrow_head_points(self._line.p2(), self._line.p1()))
        painter.drawPolygon(head)


class LineItem(QGraphicsLineItem):
    """直线：原生 QGraphicsLineItem 没有 update_end，包一层统一拖拽协议。"""

    def __init__(self, start: QPointF, end: QPointF, color: QColor,
                 width: int = DEFAULT_WIDTH):
        super().__init__(QLineF(start, end))
        self._start = start
        self.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))

    def update_end(self, end: QPointF) -> None:
        self.prepareGeometryChange()
        self.setLine(QLineF(self._start, end))


class MosaicItem(QAbstractGraphicsShapeItem):
    """像素/马赛克：拖拽区域 → 底图降采样再放大（块状化）。

    起点独立保存（同 ShapeItem 的教训）；缓存生成后的马赛克图，
    paint 只做贴图，拖拽中区域变化时重算。
    """

    def __init__(self, source: QPixmap, start: QPointF, end: QPointF):
        super().__init__()
        self._source = source
        self._start = start
        self._rect = QRectF(start, end).normalized()
        self._cache: QPixmap | None = None

    def update_end(self, end: QPointF) -> None:
        self.prepareGeometryChange()
        self._rect = QRectF(self._start, end).normalized()
        self._cache = None

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-1, -1, 1, 1)

    def _build_cache(self, dpr: float) -> QPixmap:
        src_rect = QRectF(self._rect.x() * dpr, self._rect.y() * dpr,
                          self._rect.width() * dpr, self._rect.height() * dpr).toRect()
        region = self._source.copy(src_rect)
        if region.width() < 2 or region.height() < 2:
            return region
        block_phys = max(2, int(MOSAIC_BLOCK * dpr))
        tiny = region.scaled(
            max(1, region.width() // block_phys),
            max(1, region.height() // block_phys),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        return tiny.scaled(region.width(), region.height(),
                           Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.FastTransformation)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        dpr = painter.device().devicePixelRatio()
        if self._cache is None:
            self._cache = self._build_cache(dpr)
            self._cache.setDevicePixelRatio(dpr)
        painter.drawPixmap(self._rect.topLeft(), self._cache)


def make_annotation_item(tool: str, source: QPixmap, start: QPointF, end: QPointF,
                         color: QColor = DEFAULT_COLOR,
                         width: int = DEFAULT_WIDTH) -> QGraphicsItem:
    """工厂：按工具名创建图元。"""
    match tool:
        case "rect":
            return RectItem(start, end, color, width)
        case "ellipse":
            return EllipseItem(start, end, color, width)
        case "line":
            return LineItem(start, end, color, width)
        case "arrow":
            return ArrowItem(start, end, color, width)
        case "mosaic":
            return MosaicItem(source, start, end)
        case _:
            raise ValueError(f"未知工具: {tool}")


TEXT_COLOR = QColor("#111827")
TEXT_PLACEHOLDER = "输入文字"


class TextItem(QGraphicsTextItem):
    """文本图元：占位文字全选可直接打字覆盖；失焦时空内容自动清理。"""

    def __init__(self, pos: QPointF, color: QColor | None = None):
        super().__init__()
        self.setPos(pos)
        font = QFont()
        font.setPointSize(18)
        font.setBold(True)
        self.setFont(font)
        self.setDefaultTextColor(color or TEXT_COLOR)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setPlainText(TEXT_PLACEHOLDER)
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.setTextCursor(cursor)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setTextWidth(360)  # 给初始编辑一个可视宽度

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        text = self.toPlainText().strip()
        if not text or text == TEXT_PLACEHOLDER:
            scene = self.scene()
            if scene is not None:
                scene.removeItem(self)  # 空内容不留在画面上


def make_text_item(pos: QPointF, color: QColor | None = None) -> TextItem:
    """文本工具：点击落点，占位全选，直接打字覆盖。"""
    return TextItem(pos, color)
