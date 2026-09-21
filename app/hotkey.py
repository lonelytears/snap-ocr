"""ShortcutManager（M2）：pynput 全局热键。

权限：macOS 需「辅助功能」（系统设置 → 隐私与安全性 → 辅助功能 →
勾选 snap-ocr.app 或运行它的终端）。AXIsProcessTrusted 只读预检不弹窗。

线程模型：pynput 回调发生在其监听线程，直接触碰 Qt 对象是未定义行为——
必须经 Qt Signal 桥接（跨线程 emit 自动走 QueuedConnection 回主线程）。
"""

from PySide6.QtCore import QObject, Signal

from app.config import Settings

DEFAULT_HOTKEY = "<alt>+<shift>+o"   # ⌥⇧O，可用 SNAP_HOTKEY 覆盖


def has_ax_permission() -> bool:
    """只读预检辅助功能权限（无 pyobjc 环境不阻断）。"""
    try:
        import Quartz

        return bool(Quartz.AXIsProcessTrusted())
    except Exception:  # noqa: BLE001
        return True


class _HotkeyBridge(QObject):
    """pynput 线程 → Qt 主线程的唯一通道。"""

    triggered = Signal()


class ShortcutManager:
    """持有一个 GlobalHotKeys 监听；触发即回调（已 marshal 到主线程）。"""

    def __init__(self, hotkey_str: str, callback):
        self._bridge = _HotkeyBridge()
        self._bridge.triggered.connect(callback)
        from pynput import keyboard

        self._listener = keyboard.GlobalHotKeys({hotkey_str: self._bridge.triggered.emit})

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        if self._listener.is_running():
            self._listener.stop()

    @staticmethod
    def validate(hotkey_str: str) -> bool:
        """纯函数便于单测：热键串能否被 pynput 解析。"""
        from pynput import keyboard

        try:
            keyboard.HotKey.parse(hotkey_str)
            return True
        except (ValueError, KeyError):
            return False


def setup_hotkey(settings: Settings, callback) -> "ShortcutManager | None":
    """main 接线用：有权限→启动监听；无权限→返回 None（由调用方提示）。"""
    if not has_ax_permission():
        return None
    hotkey_str = settings.hotkey or DEFAULT_HOTKEY
    if not ShortcutManager.validate(hotkey_str):
        raise ValueError(f"热键配置无法解析: {hotkey_str!r}（pynput 语法，如 <alt>+<shift>+o）")
    manager = ShortcutManager(hotkey_str, callback)
    manager.start()
    return manager
