"""标注工具栏：跟随选区下方浮动。

信号驱动，不持有业务逻辑：
  tool_selected(str)  选择绘图工具 rect/ellipse/line/arrow/mosaic/text
  undoClicked() / ocrClicked() / saveClicked() / cancelClicked()
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from app.ui.annotate.items import DEFAULT_COLOR

TOOLS = [
    ("rect", "▭ 矩形"),
    ("ellipse", "◯ 椭圆"),
    ("line", "／ 直线"),
    ("arrow", "→ 箭头"),
    ("mosaic", "▦ 像素"),
    ("text", "T 文本"),
]

COLORS = ["#ef4444", "#f59e0b", "#2563eb", "#10b981", "#111827"]


class AnnotateToolbar(QFrame):
    tool_selected = Signal(str)
    color_selected = Signal(object)   # QColor
    undo_clicked = Signal()
    ocr_clicked = Signal()
    save_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # 子控件模式：不设窗口标志——独立 Tool 窗口会与覆盖窗抢层级而"消失"
        self.setStyleSheet(
            "AnnotateToolbar { background:#1f2937; border-radius:8px; }"
            "QPushButton { color:#e5e7eb; background:transparent; border:none;"
            " padding:6px 8px; border-radius:6px; font-size:12px; }"
            "QPushButton:hover { background:#374151; }"
            "QPushButton:checked { background:#2563eb; }"
            "QPushButton:disabled { color:#6b7280; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        self._tool_btns: dict[str, QPushButton] = {}
        for tool, label in TOOLS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setToolTip(label[2:])
            btn.clicked.connect(lambda _=False, t=tool: self._pick_tool(t))
            layout.addWidget(btn)
            self._tool_btns[tool] = btn

        layout.addSpacing(6)

        # 撤销
        undo = QPushButton("↩ 撤销")
        undo.clicked.connect(self.undo_clicked)
        layout.addWidget(undo)

        layout.addSpacing(6)

        # 色点
        self._color_btns: list[QPushButton] = []
        for hex_color in COLORS:
            c = QPushButton(self)
            c.setFixedSize(16, 16)
            c.setCursor(Qt.CursorShape.PointingHandCursor)
            c.setStyleSheet(
                f"background:{hex_color}; border-radius:8px; border:"
                f"2px solid {'#ffffff' if hex_color != DEFAULT_COLOR.name() else '#fbbf24'};"
            )
            c.clicked.connect(lambda _=False, h=hex_color: self._pick_color(h))
            layout.addWidget(c)
            self._color_btns.append(c)

        layout.addSpacing(6)
        sep = QFrame(self)
        sep.setFixedSize(1, 20)
        sep.setStyleSheet("background:#4b5563;")
        layout.addWidget(sep)

        # 主操作
        ocr = QPushButton("📋 OCR")
        ocr.setStyleSheet("color:#93c5fd; font-weight:600;")
        ocr.clicked.connect(self.ocr_clicked)
        layout.addWidget(ocr)

        save = QPushButton("💾 保存")
        save.clicked.connect(self.save_clicked)
        layout.addWidget(save)

        cancel = QPushButton("✕ 取消")
        cancel.setStyleSheet("color:#fca5a5;")
        cancel.clicked.connect(self.cancel_clicked)
        layout.addWidget(cancel)

    def _pick_tool(self, tool: str) -> None:
        for t, btn in self._tool_btns.items():
            btn.setChecked(t == tool)
        self.tool_selected.emit(tool)

    def _pick_color(self, hex_color: str) -> None:
        self.color_selected.emit(QColor(hex_color))

    def clear_tool_check(self) -> None:
        for btn in self._tool_btns.values():
            btn.setChecked(False)
