"""OverlayWindow（V2 §5）：临时全屏交互层。

V1 踩坑结论的固化：
- 不能用 Tool 窗口标志（纯托盘应用非活跃时被 macOS 隐藏）；
- 显示时必须 AppKit activateIgnoringOtherApps_ 显式激活；
- 覆盖目标显示器完整区域（鼠标所在屏）。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from app.screenshot.canvas import ScreenshotCanvas


class OverlayWindow(QWidget):
    """仅存在于 Screenshot Session 生命周期内（§2.3）。"""

    def __init__(self, snapshot, parent: QWidget | None = None):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window  # 明确普通 Window：见模块注释
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setGeometry(snapshot.screen_bounds)  # 目标显示器完整区域
        self.setWindowTitle("snap-ocr session")

        self.canvas = ScreenshotCanvas(snapshot, self)
        self.canvas.setGeometry(self.rect())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except ImportError:
            pass
        self._join_all_spaces_above_fullscreen()
        self.canvas.setFocus()

    def _join_all_spaces_above_fullscreen(self) -> None:
        """盖在全屏窗口上的唯一正解（V1/V2 反复"看不到覆盖窗"的最终根因）：

        原生全屏应用独占 Space，普通置顶(StaysOnTopHint=浮动级)压不上去。
        需要：Shielding 窗口级别(系统锁屏级) + CanJoinAllSpaces/FullScreenAuxiliary。
        """
        try:
            import objc
            from AppKit import (
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
            )
            from Quartz import CGShieldingWindowLevel

            view = objc.objc_object(c_void_p=int(self.winId()))
            ns_window = view.window()
            ns_window.setLevel_(CGShieldingWindowLevel())
            ns_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            ns_window.makeKeyAndOrderFront_(None)
            ns_window.orderFrontRegardless()
        except Exception:  # noqa: BLE001 — 非 macOS/无 pyobjc 时退回普通置顶
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.canvas.setGeometry(self.rect())
