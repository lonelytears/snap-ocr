"""结果窗口体系。

- PinnedResultWindow: 贴图式主窗口（截图后立即出现：左=图片, 右=识别文本）
- ResultWindow: 纯文本窗（历史回看用）

生命周期要点：无父窗口的 QWidget 由 Python 侧持有——必须放进类级注册表，
否则局部变量被 GC 时窗口直接消失（v1 初版的真实 bug）。
"""

import time

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.history import HistoryEntry

_MAX_IMAGE_EDGE = 480  # 贴图区图片最大边(px)，防大图撑爆屏幕


class _WindowRegistry:
    """持有所有无父窗口的引用，关闭时自动移除。"""

    _windows: list["QWidget"] = []

    @classmethod
    def keep(cls, win: "QWidget") -> None:
        cls._windows.append(win)
        win.destroyed.connect(lambda *_: cls._try_discard(win))

    @classmethod
    def _try_discard(cls, win: "QWidget") -> None:
        try:
            cls._windows.remove(win)
        except ValueError:
            pass


def _scaled_pixmap(path: str) -> QPixmap:
    pm = QPixmap(path)
    if pm.isNull():
        return pm
    if max(pm.width(), pm.height()) > _MAX_IMAGE_EDGE:
        pm = pm.scaled(
            _MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pm


class PinnedResultWindow(QWidget):
    """贴图窗：截图完成立即出现，识别结果就位后填入右侧文本区。"""

    def __init__(self, image_path: str, font_pt: int = 14):
        super().__init__()
        self._image_path = image_path
        self._drag_offset: QPoint | None = None
        self._entry: HistoryEntry | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._setup_ui(font_pt)
        self._move_near_cursor()
        self.show()
        self.raise_()
        self.activateWindow()
        _WindowRegistry.keep(self)

    # ── UI ─────────────────────────────
    def _setup_ui(self, font_pt: int) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        self.setStyleSheet(
            "PinnedResultWindow { background:#ffffff; border:1px solid #d1d5db; }"
        )

        # 左：截图贴图区（可拖动句柄）
        self.image_label = QLabel()
        pm = _scaled_pixmap(self._image_path)
        if not pm.isNull():
            self.image_label.setPixmap(pm)
        self.image_label.setStyleSheet(
            "background:#f3f4f6; border:1px dashed #cbd5e1;"
        )
        self.image_label.setMinimumSize(120, 80)
        outer.addWidget(self.image_label)

        # 右：状态 + 文本 + 按钮
        right = QVBoxLayout()
        right.setSpacing(6)

        self.meta_label = QLabel("⏳ 识别中…")
        self.meta_label.setStyleSheet("color:#6b7280; font-size:11px;")
        right.addWidget(self.meta_label)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText("识别中…")
        self.text_edit.setStyleSheet(
            f"font-size:{font_pt}pt; border:none; background:transparent;"
        )
        self.text_edit.setMinimumWidth(280)
        right.addWidget(self.text_edit, stretch=1)

        btns = QHBoxLayout()
        self.copy_btn = QPushButton("复制全部")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_all)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btns.addWidget(self.copy_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        right.addLayout(btns)

        outer.addLayout(right)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close)
        QShortcut(QKeySequence.StandardKey.Copy, self, activated=self._copy_all)

    # ── 数据流 ──────────────────────────
    def set_result(self, entry: HistoryEntry) -> None:
        self._entry = entry
        self.text_edit.setPlainText(entry.text)
        self.text_edit.selectAll()
        self.meta_label.setText(
            f"⏱ {entry.latency_ms:.0f}ms · 置信度 {entry.result.avg_score:.2f}"
            f" · {entry.quality}"
        )
        self.copy_btn.setEnabled(True)

    def set_error(self, message: str) -> None:
        self.text_edit.setPlainText(f"识别失败：{message}")
        self.meta_label.setText("❌ 出错了")
        self.copy_btn.setEnabled(False)

    # ── 交互 ────────────────────────────
    def _copy_all(self) -> None:
        if self._entry is None:
            return
        QGuiApplication.clipboard().setText(self._entry.text)
        self.copy_btn.setText("已复制 ✓")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None

    def _move_near_cursor(self) -> None:
        pos = QCursor.pos()
        # 多显示器：贴着鼠标所在屏的可用区域收缩
        screen_obj = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        screen = screen_obj.availableGeometry()
        x = min(pos.x() + 16, screen.right() - self.width())
        y = min(pos.y() + 16, screen.bottom() - self.height())
        self.move(max(x, screen.left()), max(y, screen.top()))


class ResultWindow(QWidget):
    """纯文本结果窗（历史回看）。"""

    def __init__(self, entry: HistoryEntry, font_pt: int = 14):
        super().__init__()
        self._entry = entry
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(420, 200)
        self.resize(480, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        header = QLabel(
            time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
            + f"  ⏱ {entry.latency_ms:.0f}ms · {entry.quality}"
        )
        header.setStyleSheet("color:#888; font-size:11px;")
        layout.addWidget(header)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(entry.text)
        self.text_edit.setStyleSheet(f"font-size:{font_pt}pt; border:none;")
        self.text_edit.selectAll()
        layout.addWidget(self.text_edit, stretch=1)

        btns = QHBoxLayout()
        copy_btn = QPushButton("复制全部")
        copy_btn.clicked.connect(self._copy_all)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btns.addWidget(copy_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close)
        self._copy_btn_ref = copy_btn

        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - QPoint(self.width() // 2, self.height() // 2))
        self.show()
        self.raise_()
        _WindowRegistry.keep(self)

    def _copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self._entry.text)
        self._copy_btn_ref.setText("已复制 ✓")
