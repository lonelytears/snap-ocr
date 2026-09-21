"""ScreenshotCanvas（V2 §19）：单 Canvas 固定顺序渲染 + 鼠标事件。

渲染顺序（§7.2，固定不可变）：
  1. 冻结截图全屏  2. 蒙版（选区外四侧）  3. 选区镂空(重绘原图)
  4. 选区边框      5. 尺寸标签
  6. 标注层（QGraphicsScene → AnnotationView 仅覆盖选区）
  7. 工具栏（AnnotateToolbar 子控件，选区下方/上方浮动）

交互（M3+M4 集成）：
  拖框选区(四方向) → 松开出现工具栏 → 选工具后在选区内拖拽创建图元；
  文本工具=点击落点直接输入（点已有文本=再编辑）；
  撤销=移除最后图元；双击/回车=完成(copy)；工具栏 OCR/保存=完成(ocr/save)；
  Esc 分层：文本编辑中=结束编辑，否则=取消会话。
  再次拖框=放弃当前标注层（与 iShot 同语义）。
"""

import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from app.screenshot.coordinates import CoordinateManager
from app.screenshot.selection import Selection, normalized_rect

MASK_COLOR = QColor(0, 0, 0, 89)     # ≈35% 黑（§7.1 0.30~0.45）
BORDER_COLOR = QColor("#2563eb")
DEGENERATE_LEN = 4                   # 拖拽距离低于此视为误触，不生成图元


class AnnotationView(QGraphicsView):
    """选区内的标注显示/输入层（无边框透明，几何恒等于选区）。

    view 只提供场景渲染与文本编辑所需的焦点/输入上下文；
    图元创建/更新全部转发宿主 canvas 处理（§19 单一交互入口）。
    """

    def __init__(self, canvas: "ScreenshotCanvas"):
        super().__init__(canvas)
        self._canvas = canvas
        self._scene_owns = False   # 本轮按下是否归场景（文本编辑交互）
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setStyleSheet("background: transparent; border: none;")
        self.setMouseTracking(True)
        self.hide()

    def _scene_pos(self, event) -> QPointF:
        # PySide6 的 mapToScene 只收 QPoint（QPointF 重载不存在）
        return QPointF(self.mapToScene(event.position().toPoint()))

    # ── 鼠标转发（工具交互归 canvas；文本编辑交场景）──
    def mousePressEvent(self, event) -> None:
        handled = self._canvas.annotation_press(self._scene_pos(event))
        self._scene_owns = not handled
        if self._scene_owns:
            super().mousePressEvent(event)   # 场景交付：文本聚焦/光标定位
        else:
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._scene_owns:
            super().mouseMoveEvent(event)    # 文本选词拖拽
        else:
            self._canvas.annotation_move(self._scene_pos(event))
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._scene_owns:
            self._scene_owns = False
            super().mouseReleaseEvent(event)
        else:
            self._canvas.annotation_release(self._scene_pos(event))
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        from app.ui.annotate.items import TextItem

        if isinstance(self._canvas.item_at(self._scene_pos(event)), TextItem):
            super().mouseDoubleClickEvent(event)   # 场景处理文本再编辑
        else:
            self._canvas.double_click_complete.emit()
        event.accept()

    # ── 按键分层（编辑中=结束编辑，否则=会话级操作）──
    def keyPressEvent(self, event) -> None:
        editing = self._canvas.is_text_editing()
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if editing:
                self._canvas.end_text_editing()
            else:
                self._canvas.escape_cancel.emit()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if editing:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    super().keyPressEvent(event)   # Shift+Enter=换行
                else:
                    self._canvas.end_text_editing()
            elif not self._canvas.selection.is_empty():
                self._canvas.double_click_complete.emit()
            return
        super().keyPressEvent(event)


class ScreenshotCanvas(QWidget):
    """Overlay 的唯一绘制面。所有坐标为 Overlay 局部(=逻辑)坐标。"""

    double_click_complete = Signal()
    escape_cancel = Signal()

    def __init__(self, snapshot, parent: QWidget | None = None):
        super().__init__(parent)
        self._snapshot = snapshot
        self._coords = CoordinateManager(snapshot.scale_factor)
        self.selection = Selection(
            QRectF(0, 0, snapshot.image.width(), snapshot.image.height())
        )
        self._anchor = QPointF()
        self._dragging = False
        self._cursor_pos = QPointF()   # 坐标读数（选区时随光标显示）
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

        # ── 标注层（M3+M4）────────────────────
        self._scene = QGraphicsScene(self)
        self._view = AnnotationView(self)
        self._view.setScene(self._scene)
        self._tool: str | None = None
        self._color = None            # QColor，选色板后生效
        self._annot_items: list = []  # 创建顺序栈（撤销用）
        self._active_item = None      # 拖拽中的图元
        self._active_start = QPointF()

        from app.ui.annotate.toolbar import AnnotateToolbar

        self.toolbar = AnnotateToolbar(self)
        self.toolbar.tool_selected.connect(self._set_tool)
        self.toolbar.color_selected.connect(self._set_color)
        self.toolbar.undo_clicked.connect(self.undo_last)
        self.toolbar.hide()

    # ── 工具/颜色/撤销 ───────────────────────
    def _set_tool(self, tool: str) -> None:
        self._tool = tool
        self._view.setCursor(
            Qt.CursorShape.IBeamCursor if tool == "text"
            else Qt.CursorShape.CrossCursor
        )

    def _set_color(self, color) -> None:
        self._color = color

    def undo_last(self) -> None:
        """移除最后创建的图元（失焦自清理的空文本已不在场景，跳过保留栈序）。"""
        while self._annot_items:
            item = self._annot_items.pop()
            if item.scene() is not None:
                self._scene.removeItem(item)
                return

    def item_at(self, pos: QPointF):
        from PySide6.QtGui import QTransform

        return self._scene.itemAt(pos, QTransform())

    def is_text_editing(self) -> bool:
        from app.ui.annotate.items import TextItem

        return isinstance(self._scene.focusItem(), TextItem)

    def end_text_editing(self) -> bool:
        """结束文本编辑（失焦触发空内容自清理）。无编辑返回 False。"""
        from app.ui.annotate.items import TextItem

        focus = self._scene.focusItem()
        if not isinstance(focus, TextItem):
            return False
        focus.clearFocus()
        return True

    def _add_item(self, item) -> None:
        self._scene.addItem(item)
        self._annot_items.append(item)

    # ── 标注创建（由 AnnotationView 转发；返回 False=事件归场景）──
    def annotation_press(self, pos: QPointF) -> bool:
        from app.ui.annotate.items import TextItem, make_annotation_item, make_text_item

        if self._tool is None:
            return False
        existing = self.item_at(pos)
        if isinstance(existing, TextItem):
            return False  # 点已有文本=交场景聚焦编辑
        if self._tool == "text":
            item = make_text_item(pos, self._color)
            self._add_item(item)
            self._scene.setFocusItem(item)
            self._view.setFocus(Qt.FocusReason.MouseFocusReason)
            return True
        self._active_start = QPointF(pos)
        self._active_item = make_annotation_item(
            self._tool, self._snapshot.image, pos, pos, self._color or QColor("#ef4444")
        )
        self._add_item(self._active_item)
        return True

    def annotation_move(self, pos: QPointF) -> None:
        if self._active_item is not None:
            self._active_item.update_end(pos)

    def annotation_release(self, pos: QPointF) -> None:
        item, self._active_item = self._active_item, None
        if item is None:
            return
        if (pos - self._active_start).manhattanLength() < DEGENERATE_LEN:
            self._scene.removeItem(item)
            self._annot_items.remove(item)

    # ── 标注层显隐/摆位 ──────────────────────
    def _show_annotation_layer(self) -> None:
        sel = self.selection.rect
        self._view.setGeometry(sel.toAlignedRect())
        self._view.setSceneRect(sel)
        self._view.show()
        self._view.raise_()
        self._place_toolbar()
        self.toolbar.show()
        self.toolbar.raise_()

    def _hide_annotation_layer(self) -> None:
        self._view.hide()
        self.toolbar.hide()

    def _place_toolbar(self) -> None:
        """选区下方居中；底部放不下翻到选区上方。"""
        sel = self.selection.rect
        tb = self.toolbar
        tb.adjustSize()
        w, h = tb.width(), tb.height()
        x = int(sel.center().x() - w / 2)
        x = max(4, min(x, self.width() - w - 4))
        y = int(sel.bottom()) + 12
        if y + h > self.height() - 4:
            y = int(sel.top()) - h - 12
        tb.move(max(4, x), max(4, y))

    def _reset_annotations(self) -> None:
        """新拖框=放弃当前标注层（含工具选择复位）。"""
        self._scene.clear()
        self._annot_items.clear()
        self._active_item = None
        self._tool = None
        self.toolbar.clear_tool_check()
        self._hide_annotation_layer()

    # ── 渲染（§19 固定顺序）────────────────────
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        img = self._snapshot.image
        # 1. 冻结截图铺满（局部原点 = 屏幕原点）
        painter.drawPixmap(0, 0, img)

        # 调试网格（SNAP_DEBUG_GRID=1）：200px 刻度线+标数，供人工对位
        if os.environ.get("SNAP_DEBUG_GRID") == "1":
            self._paint_grid(painter)

        sel = self.selection.rect
        if not sel.isNull():
            full = QRectF(0, 0, self.width(), self.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(MASK_COLOR))
            # 2. 蒙版：四侧矩形（避免全屏涂黑再挖洞的整屏重绘）
            top = QRectF(full.left(), full.top(), full.width(), sel.top() - full.top())
            bottom = QRectF(full.left(), sel.bottom(), full.width(),
                            full.bottom() - sel.bottom())
            left = QRectF(full.left(), sel.top(), sel.left() - full.left(), sel.height())
            right = QRectF(sel.right(), sel.top(), full.right() - sel.right(),
                           sel.height())
            for side in (top, bottom, left, right):
                if side.width() > 0 and side.height() > 0:
                    painter.drawRect(side)
            # 3. 选区镂空：把原图该区域重绘为清晰画面
            #    drawPixmap 的 source 矩形按 QPixmap 物理像素解释（target 才是
            #    画布逻辑坐标）——传逻辑 rect 会把选区左上四分之一放大 2x 画进
            #    框内，正是"框的与得到的不一致"的偏移根因，必须转物理。
            painter.drawPixmap(sel.toRect(), img, self._coords.to_pixel_rect(sel))
            # 4. 选区边框
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(BORDER_COLOR, 2))
            painter.drawRect(sel)
            # 5. 尺寸标签
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("", 11))
            label = self.selection.size_label()
            lx = min(sel.left(), sel.right()) + 2
            ly = max(0.0, min(sel.top(), sel.bottom()) - 20)
            painter.drawText(QPointF(lx, ly + 14), label)

            # 6. 操作提示（选区下方居中；完成手势的可发现性）
            hint = "双击选区或按回车完成 · Esc 取消"
            fm = painter.fontMetrics()
            hw = fm.horizontalAdvance(hint)
            hx = max(0, min(int(sel.center().x() - hw / 2), self.width() - hw - 8))
            hy = min(int(sel.bottom()) + 18, self.height() - 8)
            if not self.toolbar.isVisible():
                painter.drawText(QPointF(hx, hy), hint)

        # 光标坐标读数（拖拽时显示，便于精确反馈/对位）
        if self._dragging:
            painter.setPen(QColor("#fbbf24"))
            painter.setFont(QFont("", 11))
            tx = min(self._cursor_pos.x() + 14, self.width() - 110)
            ty = max(18.0, self._cursor_pos.y() - 10)
            painter.drawText(
                QPointF(tx, ty),
                f"{int(self._cursor_pos.x())}, {int(self._cursor_pos.y())}",
            )
        # 7. 标注层 = AnnotationView 子控件（QGraphicsView 自绘），此处无操作
        painter.end()

    def _paint_grid(self, painter: QPainter) -> None:
        """调试网格：横竖 200px 线 + 左上角标数，画在最上层。"""
        pen = QPen(QColor(255, 191, 0, 170), 1)  # 半透明琥珀色
        painter.setPen(pen)
        painter.setFont(QFont("", 10))
        step = 200
        for x in range(step, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
            painter.drawText(QPointF(x + 4, 16), str(x))
        for y in range(step, self.height(), step):
            painter.drawLine(0, y, self.width(), y)
            painter.drawText(QPointF(4, y - 4), str(y))

    # ── 鼠标（§14 HitTest；选区创建）──────────
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._reset_annotations()   # 新拖框=放弃当前标注层
            self._anchor = QPointF(event.position())
            self._dragging = True

    def mouseMoveEvent(self, event) -> None:
        self._cursor_pos = QPointF(event.position())
        if self._dragging:
            self.selection.set_rect(normalized_rect(self._anchor, QPointF(event.position())))
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            if self.selection.is_empty():  # 低于最小尺寸视为误触，清空重来
                self.selection.set_rect(QRectF())
            else:
                self._show_annotation_layer()   # M3+M4：选区就绪 → 标注层
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        if self.selection.is_empty():
            return
        # 宽容判定：选区外扩 12px 内的双击都算完成（点在边框上是自然手势）
        if self.selection.rect.adjusted(-12, -12, 12, 12).contains(
            QPointF(event.position())
        ):
            self.double_click_complete.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.end_text_editing():
                return
            self.escape_cancel.emit()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not self.selection.is_empty():
            if self.end_text_editing():
                return
            self.double_click_complete.emit()

    # ── 导出（§13.1：只有截图内容+标注，无任何 UI 元素）──
    def render_final(self):
        """选区裁剪（物理分辨率）+ 标注层合成。"""
        if self.selection.is_empty():
            return None
        img = self._snapshot.image
        sel = self.selection.rect
        pixel_rect = self._coords.to_pixel_rect(sel)
        # 统一返回 QImage：QClipboard.setImage 只收 QImage（V2 的真凶 bug——
        # 返回 QPixmap 导致每次完成都在写剪贴板时 TypeError，用户贴到的是旧图）
        crop = img.copy(pixel_rect).toImage()
        if self._annot_items:
            # 标注合成：场景逻辑坐标 → 裁剪图（dpr 已设，painter 走逻辑坐标）
            crop.setDevicePixelRatio(self._coords.scale)
            painter = QPainter(crop)
            self._scene.render(
                painter,
                QRectF(0, 0, sel.width(), sel.height()),  # target：裁剪图全幅
                sel,                                       # source：场景中的选区
            )
            painter.end()
        crop.save("/tmp/snap_crop_last.png")                       # 诊断留档
        self._snapshot.image.toImage().save("/tmp/snap_frozen_last.png")
        return crop
