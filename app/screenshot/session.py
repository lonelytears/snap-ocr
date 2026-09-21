"""ScreenshotSession 与 ScreenshotManager（V2 §3/§17/§18）。

Session = 一段从"开始截图"到"完成/取消"的临时屏幕交互会话（§32）。
Manager 只做编排（§18）：不含绘制、不含 OCR、不含标注。
"""

import enum

from PySide6.QtCore import QEventLoop, QObject, Signal


class SessionState(enum.Enum):
    IDLE = "idle"
    CAPTURING = "capturing"
    SELECTING = "selecting"
    SELECTED = "selected"
    COMPLETING = "completing"
    CANCELLED = "cancelled"


class SessionResult:
    """会话产物（§13）。action: copy=进剪贴板 / ocr=调识别 / save=另存。"""

    def __init__(self, image=None, cancelled: bool = True, action: str = "copy"):
        self.image = image          # QImage：选区裁剪(含标注层)
        self.cancelled = cancelled
        self.action = action


class ScreenshotSession(QObject):
    """单个截图会话：管理状态与 Overlay 生命周期。"""

    finished = Signal(object)  # SessionResult

    def __init__(self, overlay_window, preferred_action: str = "copy",
                 parent: QObject | None = None):
        super().__init__(parent)
        self._overlay = overlay_window
        self._preferred = preferred_action   # 手势完成（双击/回车）的默认动作
        self._state = SessionState.CAPTURING
        canvas = overlay_window.canvas
        canvas.double_click_complete.connect(self.complete)
        canvas.escape_cancel.connect(self.cancel)
        # 工具栏主操作（§18 编排职责）：OCR/保存=带动作完成，取消=取消
        toolbar = getattr(canvas, "toolbar", None)
        if toolbar is not None:
            toolbar.ocr_clicked.connect(lambda: self.complete("ocr"))
            toolbar.save_clicked.connect(lambda: self.complete("save"))
            toolbar.cancel_clicked.connect(self.cancel)

    def state(self) -> SessionState:
        return self._state

    def start(self) -> None:
        self._state = SessionState.SELECTING
        self._overlay.show()

    def cancel(self) -> None:
        """任意状态 Esc → 销毁 Overlay → 恢复桌面（§3）。"""
        if self._state in (SessionState.CANCELLED, SessionState.COMPLETING):
            return
        self._state = SessionState.CANCELLED
        self._teardown()
        self.finished.emit(SessionResult(cancelled=True))

    def complete(self, action: str | None = None) -> None:
        if self._state is not SessionState.SELECTED and \
                self._state is not SessionState.SELECTING:
            return
        self._state = SessionState.COMPLETING
        image = self._overlay.canvas.render_final()
        self._teardown()
        self.finished.emit(
            SessionResult(image=image, cancelled=image is None,
                          action=action or self._preferred)
        )

    def _teardown(self) -> None:
        self._state = SessionState.IDLE
        self._overlay.close()
        self._overlay.deleteLater()


class ScreenshotManager(QObject):
    """编排入口（§18）：托盘只跟它说话。"""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._active: ScreenshotSession | None = None

    def is_active(self) -> bool:
        return self._active is not None

    def start_session(self, preferred_action: str = "copy") -> SessionResult:
        """同步会话：嵌套事件循环直到完成/取消（托盘事件仍可处理）。"""
        if self._active is not None:
            return SessionResult(cancelled=True)  # 防重入

        from app.screenshot.capture import ScreenCapture
        from app.screenshot.overlay import OverlayWindow

        snapshot = ScreenCapture().capture()  # Capture 先于 Overlay（§2.1）
        overlay = OverlayWindow(snapshot)
        session = ScreenshotSession(overlay, preferred_action=preferred_action)
        self._active = session

        holder: dict = {}
        session.finished.connect(lambda r: holder.update(result=r))
        loop = QEventLoop()
        session.finished.connect(loop.quit)
        session.start()
        loop.exec()

        self._active = None
        return holder.get("result") or SessionResult(cancelled=True)


def has_screen_permission() -> bool:
    """只读预检屏幕录制权限（不触发授权弹窗、不阻塞启动路径）。"""
    try:
        import Quartz

        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:  # noqa: BLE001 — 无 pyobjc/Quartz 环境不阻断
        return True
