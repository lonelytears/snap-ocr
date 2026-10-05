"""更新弹窗：非强制（可稍后）/ 强制（只能更新或退出）双模式。

强制模式拦截一切关闭路径（reject/closeEvent/Esc）——LSUIElement 应用无
Dock 图标，弹窗一旦被压到后面就找不回，故强制模式同时置顶。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.updater import Appcast

_BLUE = "#2563eb"
_GRAY = "#6b7280"


class UpdateDialog(QDialog):
    update_requested = Signal()   # 「立即更新」（两种模式共用）

    def __init__(self, appcast: Appcast, forced: bool, parent=None):
        super().__init__(parent)
        self._forced = forced
        self.setWindowTitle("snap-ocr 更新")
        self.setModal(True)
        self.resize(440, 320)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet("UpdateDialog { background:#ffffff; }")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)

        title = QLabel(f"{'⚠ 需要更新后才能继续使用' if forced else '发现新版本'}"
                       f"　{appcast.version}")
        title.setStyleSheet("color:#111827; font-size:16px; font-weight:600;")
        lay.addWidget(title)

        if appcast.notes:
            current = QLabel("更新内容：")
            current.setStyleSheet(f"color:{_GRAY}; font-size:12px;")
            lay.addWidget(current)
            notes = QPlainTextEdit()
            notes.setReadOnly(True)
            notes.setPlainText("\n".join(f"· {n}" for n in appcast.notes))
            notes.setStyleSheet(
                "color:#374151; font-size:13px; background:#f9fafb;"
                " border:1px solid #ececec; border-radius:6px; padding:8px;"
            )
            lay.addWidget(notes, stretch=1)
        else:
            lay.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(True)
        self.progress.setStyleSheet(
            f"QProgressBar {{ border:1px solid #d1d5db; border-radius:6px;"
            f" height:18px; text-align:center; font-size:12px; color:#374151; }}"
            f"QProgressBar::chunk {{ background:{_BLUE}; border-radius:5px; }}"
        )
        lay.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{_GRAY}; font-size:12px;")
        self.status.setVisible(False)
        lay.addWidget(self.status)

        buttons = QWidget()
        row = QVBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.update_btn = QPushButton("立即更新")
        self.update_btn.setStyleSheet(
            f"background:{_BLUE}; color:#ffffff; border-radius:6px;"
            " padding:8px 0; font-size:14px;"
        )
        self.update_btn.clicked.connect(lambda: self.update_requested.emit())
        row.addWidget(self.update_btn)

        self.later_btn = QPushButton("退出应用" if forced else "稍后")
        self.later_btn.setStyleSheet(
            f"color:{_GRAY}; border:1px solid #d1d5db; border-radius:6px;"
            " padding:6px 0; font-size:13px; background:#ffffff;"
        )
        if forced:
            from PySide6.QtWidgets import QApplication

            self.later_btn.clicked.connect(QApplication.quit)
        else:
            self.later_btn.clicked.connect(self.accept)
        row.addWidget(self.later_btn)
        lay.addWidget(buttons)

    # ── 下载/安装状态 ──────────────────────────
    def set_progress(self, pct: int) -> None:
        self.progress.setVisible(True)
        self.progress.setValue(pct)
        self._status(f"下载中 {pct}%")
        self.update_btn.setEnabled(False)

    def set_failed(self, message: str) -> None:
        """下载/校验失败：回到可重试状态（强制模式留在弹窗内）。"""
        self.progress.setVisible(False)
        self._status(message, error=True)
        self.update_btn.setEnabled(True)
        self.update_btn.setText("重试更新")

    def set_installing(self) -> None:
        self.progress.setVisible(False)
        self.update_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        self._status("安装中，应用即将重启…")

    def _status(self, text: str, error: bool = False) -> None:
        self.status.setVisible(True)
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color:{'#ef4444' if error else _GRAY}; font-size:12px;"
        )

    # ── 强制模式：拦截一切关闭 ──────────────────────────
    def reject(self) -> None:  # noqa: D102 - Esc/代码路径
        if not self._forced:
            super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._forced:
            event.ignore()
        else:
            super().closeEvent(event)
