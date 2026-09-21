"""Selection（V2 §8）：选区数据与几何操作，纯逻辑无 UI 依赖。

- 任意方向拖拽（四方向）→ 统一 normalize；
- 移动（§8.3）与 8 向缩放（§8.4，Phase 2 启用手柄，几何先备好）；
- 最小尺寸 20×20（§8.5）。
"""

import enum

from PySide6.QtCore import QPointF, QRectF

MIN_WIDTH = 20
MIN_HEIGHT = 20


def normalized_rect(a: QPointF, b: QPointF) -> QRectF:
    """任意方向两点 → 标准化矩形（Case 3 的核心）。"""
    return QRectF(a, b).normalized()


class HandlePos(enum.Enum):
    """8 向调整柄（§8.4）。"""

    NW = "nw"
    N = "n"
    NE = "ne"
    W = "w"
    E = "e"
    SW = "sw"
    S = "s"
    SE = "se"


HANDLE_SIZE = 8  # 命中半径(px, 逻辑)


class Selection:
    def __init__(self, bounds: QRectF):
        self._bounds = bounds      # 可选范围（整屏逻辑坐标）
        self._rect = QRectF()      # 空选区

    # ── 基本属性 ─────────────────────────
    @property
    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def is_empty(self) -> bool:
        return self._rect.isNull() or self._rect.width() < MIN_WIDTH \
            or self._rect.height() < MIN_HEIGHT

    def set_rect(self, rect: QRectF) -> None:
        self._rect = self._clamp(rect.normalized())

    def _clamp(self, rect: QRectF) -> QRectF:
        return rect.intersected(self._bounds)

    # ── 移动（§8.3）──────────────────────
    def move_by(self, delta: QPointF) -> None:
        r = self._rect.translated(delta)
        # 撞边界时贴合，不允许移出屏幕
        if r.left() < self._bounds.left():
            r.moveLeft(self._bounds.left())
        if r.top() < self._bounds.top():
            r.moveTop(self._bounds.top())
        if r.right() > self._bounds.right():
            r.moveRight(self._bounds.right())
        if r.bottom() > self._bounds.bottom():
            r.moveBottom(self._bounds.bottom())
        self._rect = r

    # ── 缩放（§8.4）─────────────────────
    def resize_by_handle(self, handle: HandlePos, delta: QPointF) -> None:
        r = QRectF(self._rect)
        dx, dy = delta.x(), delta.y()
        if "w" in handle.value:
            r.setLeft(r.left() + dx)
        if "e" in handle.value:
            r.setRight(r.right() + dx)
        if "n" in handle.value:
            r.setTop(r.top() + dy)
        if "s" in handle.value:
            r.setBottom(r.bottom() + dy)
        # 最小尺寸保护
        if r.width() < MIN_WIDTH:
            if "w" in handle.value:
                r.setLeft(r.right() - MIN_WIDTH)
            else:
                r.setRight(r.left() + MIN_WIDTH)
        if r.height() < MIN_HEIGHT:
            if "n" in handle.value:
                r.setTop(r.bottom() - MIN_HEIGHT)
            else:
                r.setBottom(r.top() + MIN_HEIGHT)
        self._rect = self._clamp(r)

    # ── 命中测试（§14 HitTest）──────────────
    def hit_test(self, p: QPointF) -> HandlePos | str | None:
        """返回 HandlePos / 'inside' / None。"""
        if self.is_empty():
            return None
        r = self._rect
        near_l = abs(p.x() - r.left()) <= HANDLE_SIZE
        near_r = abs(p.x() - r.right()) <= HANDLE_SIZE
        near_t = abs(p.y() - r.top()) <= HANDLE_SIZE
        near_b = abs(p.y() - r.bottom()) <= HANDLE_SIZE
        inside_x = r.left() + HANDLE_SIZE < p.x() < r.right() - HANDLE_SIZE
        inside_y = r.top() + HANDLE_SIZE < p.y() < r.bottom() - HANDLE_SIZE

        if near_t and near_l:
            return HandlePos.NW
        if near_t and near_r:
            return HandlePos.NE
        if near_b and near_l:
            return HandlePos.SW
        if near_b and near_r:
            return HandlePos.SE
        if near_t and inside_x:
            return HandlePos.N
        if near_b and inside_x:
            return HandlePos.S
        if near_l and inside_y:
            return HandlePos.W
        if near_r and inside_y:
            return HandlePos.E
        if r.contains(p):
            return "inside"
        return None

    def size_label(self) -> str:
        return f"{int(self._rect.width())} × {int(self._rect.height())}"
