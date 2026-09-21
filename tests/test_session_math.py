"""V2 Phase 1 单测：Selection 几何 / 坐标换算 / 抓屏命令（纯逻辑，无 GUI）。"""

from PySide6.QtCore import QPointF, QRect, QRectF

from app.screenshot.capture import build_capture_command
from app.screenshot.coordinates import CoordinateManager
from app.screenshot.selection import (
    MIN_HEIGHT,
    MIN_WIDTH,
    HandlePos,
    Selection,
    normalized_rect,
)

BOUNDS = QRectF(0, 0, 1000, 600)


# ── §8.1 任意方向框选（Case 3）─────────────────────
def test_normalized_rect_all_directions():
    lt, rb = QPointF(100, 100), QPointF(300, 200)
    assert normalized_rect(lt, rb) == QRectF(100, 100, 200, 100)
    assert normalized_rect(rb, lt) == QRectF(100, 100, 200, 100)   # 右下→左上
    assert normalized_rect(QPointF(300, 100), QPointF(100, 200)) == QRectF(100, 100, 200, 100)
    assert normalized_rect(QPointF(100, 200), QPointF(300, 100)) == QRectF(100, 100, 200, 100)


# ── §8.5 最小尺寸 ─────────────────────────────
def test_selection_min_size():
    sel = Selection(BOUNDS)
    sel.set_rect(QRectF(0, 0, 5, 5))
    assert sel.is_empty()
    sel.set_rect(QRectF(0, 0, MIN_WIDTH, MIN_HEIGHT))
    assert not sel.is_empty()


# ── §8.3 移动与边界贴合 ─────────────────────────
def test_selection_move_clamps_to_bounds():
    sel = Selection(BOUNDS)
    sel.set_rect(QRectF(100, 100, 200, 100))
    sel.move_by(QPointF(-500, -500))
    r = sel.rect
    assert r.topLeft() == BOUNDS.topLeft()          # 不允许移出屏幕
    sel.move_by(QPointF(5000, 5000))
    assert sel.rect.bottomRight() == BOUNDS.bottomRight()


# ── §8.4 8 向缩放 + 最小尺寸保护 ────────────────────
def test_resize_handle_min_guard():
    sel = Selection(BOUNDS)
    sel.set_rect(QRectF(100, 100, 200, 100))
    sel.resize_by_handle(HandlePos.SE, QPointF(-1000, -1000))  # 往回拖到极限
    assert sel.rect.width() >= MIN_WIDTH and sel.rect.height() >= MIN_HEIGHT


def test_resize_handle_moves_only_one_side():
    sel = Selection(BOUNDS)
    sel.set_rect(QRectF(100, 100, 200, 100))
    sel.resize_by_handle(HandlePos.E, QPointF(50, 999))  # E 柄: y 应被忽略
    assert sel.rect.width() == 250
    assert sel.rect.y() == 100 and sel.rect.height() == 100


# ── §14 HitTest ─────────────────────────────
def test_hit_test():
    sel = Selection(BOUNDS)
    sel.set_rect(QRectF(100, 100, 200, 100))
    assert sel.hit_test(QPointF(103, 103)) is HandlePos.NW
    assert sel.hit_test(QPointF(200, 150)) == "inside"
    assert sel.hit_test(QPointF(500, 500)) is None


# ── §15 坐标换算 ─────────────────────────────
def test_coordinate_manager():
    cm = CoordinateManager(scale_factor=2.0)
    assert cm.to_pixel_rect(QRectF(10, 20, 100, 50)) == QRect(20, 40, 200, 100)
    assert cm.to_logical_rect(QRect(20, 40, 200, 100)) == QRectF(10, 20, 100, 50)


# ── §6 屏幕捕获命令 ───────────────────────────
def test_capture_command():
    cmd = build_capture_command(QRect(0, 0, 2560, 1440), "/tmp/x.png")
    assert cmd[:2] == ["screencapture", "-x"]
    assert "-R" in cmd and "0,0,2560,1440" == cmd[cmd.index("-R") + 1]
    assert cmd[-1] == "/tmp/x.png"
