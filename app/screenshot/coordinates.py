"""CoordinateManager（V2 §15）：逻辑坐标与物理像素的唯一换算点。

约定：Canvas/选区/标注几何全部使用【逻辑坐标】（与 Overlay 局部坐标一致，
因为 Overlay 原点即目标屏原点）；仅在 导出/OCR crop 时经此处转物理像素。
业务代码禁止出现裸 ×2（§15.2）。
"""

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF


class CoordinateManager:
    def __init__(self, scale_factor: float):
        self.scale = scale_factor

    # ── 逻辑 → 物理（导出、裁剪） ─────────────────
    def to_pixel_rect(self, logical: QRectF) -> QRect:
        return QRect(
            int(logical.x() * self.scale),
            int(logical.y() * self.scale),
            int(logical.width() * self.scale),
            int(logical.height() * self.scale),
        )

    def to_pixel_point(self, p: QPointF) -> QPoint:
        return QPoint(int(p.x() * self.scale), int(p.y() * self.scale))

    # ── 物理 → 逻辑（极少用：解析外部图像几何） ──────
    def to_logical_rect(self, pixel: QRect) -> QRectF:
        return QRectF(
            pixel.x() / self.scale, pixel.y() / self.scale,
            pixel.width() / self.scale, pixel.height() / self.scale,
        )

    @staticmethod
    def clamp_rect(rect: QRectF, bounds: QRectF) -> QRectF:
        """把矩形约束在边界内（选区移动/缩放防越界）。"""
        return rect.intersected(bounds)
