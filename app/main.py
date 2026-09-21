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

    # M2: 全局热键（pynput 监听线程经 Signal 桥回主线程）
    from app.hotkey import setup_hotkey

    hotkey_manager = setup_hotkey(settings, tray.start_flow)
    if hotkey_manager is None:
        from PySide6.QtWidgets import QSystemTrayIcon

        tray.showMessage(
            "全局热键不可用",
            "缺少「辅助功能」权限：系统设置 → 隐私与安全性 → 辅助功能 → "
            "勾选 snap-ocr（或运行它的终端/App）后重启。\n"
            f"仍可用托盘菜单触发截图（目标热键 {settings.hotkey}）。",
            QSystemTrayIcon.MessageIcon.Warning,
        )

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
