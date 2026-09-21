"""标注集成测试（M3+M4）：canvas 标注层 + SessionResult.action + toolbar 编排。

QGraphicsScene/QGraphicsView 只做逻辑驱动（不 show），像素校验走 render_final。
"""

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.screenshot.canvas import ScreenshotCanvas
from app.screenshot.session import ScreenshotSession, SessionResult


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeSnap:
    """合成冻结图：物理 400x200，dpr=2（逻辑 200x100）。"""

    def __init__(self):
        img = QPixmap(400, 200)
        img.fill(QColor("#808080"))
        img.setDevicePixelRatio(2.0)
        self.image = img
        self.screen_bounds = QRect(0, 0, 200, 100)
        self.scale_factor = 2.0


@pytest.fixture
def canvas(qapp):
    c = ScreenshotCanvas(FakeSnap())
    c.resize(200, 100)
    c.selection.set_rect(QRectF(0, 0, 200, 100))   # 全选
    yield c
    c.deleteLater()


def _make_line(canvas, x1, y1, x2, y2):
    """走真实交互路径创建一条红线（工具→拖拽）。"""
    canvas._set_tool("line")
    canvas.annotation_press(QPointF(x1, y1))
    canvas.annotation_move(QPointF(x2, y2))
    canvas.annotation_release(QPointF(x2, y2))


def test_render_final_composites_annotation(canvas):
    _make_line(canvas, 10, 20, 180, 20)
    crop = canvas.render_final()
    assert crop is not None and crop.width() == 400 and crop.height() == 200
    ci = QImage(crop)
    # 线在逻辑 y=20 → 物理 y≈40，中心点应为红色
    c = ci.pixelColor(200, 40)
    assert c.red() > 200 and c.green() < 80 and c.blue() < 80, \
        f"got {c.red()},{c.green()},{c.blue()}"
    # 线外区域仍是灰底
    c2 = ci.pixelColor(200, 120)
    assert abs(c2.red() - 128) < 12 and abs(c2.green() - 128) < 12


def test_undo_removes_last(canvas):
    _make_line(canvas, 10, 20, 180, 20)
    assert len(canvas._annot_items) == 1
    canvas.undo_last()
    assert canvas._annot_items == []
    assert not canvas._scene.items()


def test_degenerate_drag_creates_nothing(canvas):
    _make_line(canvas, 50, 50, 51, 51)   # < 4px 位移
    assert canvas._annot_items == []


def test_new_selection_resets_annotations(canvas):
    _make_line(canvas, 10, 20, 180, 20)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPointF(5, 5).toPoint())
    assert canvas._annot_items == []
    assert not canvas._scene.items()


def test_session_result_action_default():
    r = SessionResult()
    assert r.action == "copy" and r.cancelled
    r2 = SessionResult(image=object(), cancelled=False, action="ocr")
    assert r2.action == "ocr"


def test_toolbar_wires_into_session(qapp):
    from app.screenshot.overlay import OverlayWindow

    overlay = OverlayWindow(FakeSnap())
    session = ScreenshotSession(overlay)
    got: dict = {}
    session.finished.connect(lambda r: got.update(result=r))
    session.start()   # CAPTURING → SELECTING（complete 的状态守卫要求）
    overlay.canvas.selection.set_rect(QRectF(0, 0, 150, 60))

    overlay.canvas.toolbar.ocr_clicked.emit()
    assert got["result"].action == "ocr" and not got["result"].cancelled
    assert got["result"].image is not None and got["result"].image.width() == 300
