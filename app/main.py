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

    from app.login_item import app_bundle_path
    from app.updater import UpdateController, cleanup_stale_staging

    # 上次更新若中途失败会留 .old-* 备份与 staging 残留，启动时清掉
    cleanup_stale_staging(app_bundle_path())
    updater = UpdateController(settings)

    tray = TrayApp(settings, client, fallback=SystemBackend(), updater=updater)
    tray.show()

    # 全局热键：controller 装配（pynput 线程经 Signal 桥回主线程）；
    # 设置面板改键/改 OCR 地址后由 settings_reloaded 触发重装
    from app.hotkey import HotkeyController

    hotkeys = HotkeyController(tray.start_flow)
    from app.hotkey import log_hotkey_status

    ok = hotkeys.rebuild()
    log_hotkey_status("startup", ok, f"pid={__import__('os').getpid()}")
    if not ok:
        from PySide6.QtWidgets import QSystemTrayIcon

        from app.hotkey import request_input_permission

        request_input_permission()   # 原生弹窗授权（比手动到设置里添加可靠）
        tray.showMessage(
            "全局热键待授权",
            "已在屏幕上弹出系统授权请求（或稍后弹出）——点「允许/打开系统设置」"
            "开启 snap-ocr 后，重启本应用即可使用快捷键。\n"
            "仍可用托盘菜单触发截图。",
            QSystemTrayIcon.MessageIcon.Warning,
        )

    def _on_settings_reloaded(_s):
        from PySide6.QtWidgets import QSystemTrayIcon

        ok2 = hotkeys.rebuild()
        log_hotkey_status("rebind", ok2)
        if not ok2:
            tray.showMessage(
                "全局热键未生效",
                "缺少「辅助功能」权限或热键配置无效（系统设置 → 隐私与安全性 → "
                "辅助功能 → 勾选 snap-ocr 后重启）。",
                QSystemTrayIcon.MessageIcon.Warning,
            )

    tray.settings_reloaded.connect(_on_settings_reloaded)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
