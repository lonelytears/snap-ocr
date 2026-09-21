"""入口：uv run python -m app.main（托盘常驻）。

V2 架构（Screenshot Session V2 设计文档 §32）：
  TrayApp + ScreenCapture + ScreenshotSession + Fullscreen Overlay
  + Selection Canvas + (Phase3+) Annotation Layer + OCR Client + History

启动路径零阻塞：不做权限预检、不弹模态框（V1 教训）。
"""

import signal
import sys

from PySide6.QtWidgets import QApplication

from app.capture.screencapture import SystemBackend
from app.config import get_settings
from app.ocr_client import OCRClient
from app.ui.tray import TrayApp


def main() -> int:
    settings = get_settings()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("snap-ocr")
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # 托盘工具应无 Dock 图标；Accessory 策略也更易在全屏空间取得焦点
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
    except ImportError:
        pass

    client = OCRClient(settings.ocr_base_url, settings.ocr_timeout_s)
    tray = TrayApp(settings, client, fallback=SystemBackend())
    tray.show()

    # Phase 2+: ShortcutManager(全局热键)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
