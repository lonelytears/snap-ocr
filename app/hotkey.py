"""热键体系（M2+）：多热键监听 + 可动态重装（设置面板改键后生效）。

权限：macOS 需「辅助功能」。AXIsProcessTrusted 只读预检不弹窗。

线程模型：pynput 回调发生在其监听线程，直接触碰 Qt 对象是未定义行为——
经 Qt Signal(object) 桥接（跨线程 emit 自动 QueuedConnection 回主线程，
载荷是 callable，主线程侧统一 invoke）。
"""

from PySide6.QtCore import QObject, Signal


def has_ax_permission() -> bool:
    """只读预检辅助功能权限（无 pyobjc 环境不阻断）。"""
    try:
        import Quartz

        return bool(Quartz.AXIsProcessTrusted())
    except Exception:  # noqa: BLE001
        return True


class _HotkeyBridge(QObject):
    """pynput 线程 → Qt 主线程的唯一通道。"""

    triggered = Signal(object)   # 载荷为 callable

    def __init__(self):
        super().__init__()
        self.triggered.connect(lambda fn: fn())


class ShortcutManager:
    """多热键监听：{pynput 热键串: 无参回调}；触发即回调（已 marshal 主线程）。"""

    def __init__(self, hotkeys: dict[str, object]):
        if not hotkeys:
            raise ValueError("hotkeys 不能为空")
        from pynput import keyboard

        self._bridge = _HotkeyBridge()
        self._listener = keyboard.GlobalHotKeys({
            key: (lambda fn=cb: self._bridge.triggered.emit(fn))
            for key, cb in hotkeys.items()
        })

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        # pynput Listener 无 is_running()；stop() 对未启动/已停止均幂等安全
        try:
            self._listener.stop()
        except Exception:  # noqa: BLE001 — 停止失败不阻断重装流程
            pass

    @staticmethod
    def validate(hotkey_str: str) -> bool:
        """纯函数便于单测：热键串能否被 pynput 解析。"""
        from pynput import keyboard

        try:
            keyboard.HotKey.parse(hotkey_str)
            return True
        except (ValueError, KeyError):
            return False


class HotkeyController:
    """按当前 Settings 装配热键；设置变更后 rebuild() 即换绑生效。"""

    def __init__(self, action_dispatcher):
        """action_dispatcher(action: str)：热键触发时回调（主线程）。"""
        self._dispatch = action_dispatcher
        self._manager: ShortcutManager | None = None

    def rebuild(self) -> bool:
        """重装热键。返回是否处于可用状态（有权限且有有效热键）。"""
        from app.config import get_settings

        if self._manager is not None:
            self._manager.stop()
            self._manager = None
        if not has_ax_permission():
            return False
        s = get_settings()
        hotkeys: dict[str, object] = {}
        for key_str, action in ((s.hotkey, "copy"), (s.hotkey_ocr, "ocr")):
            if key_str and ShortcutManager.validate(key_str):
                hotkeys[key_str] = lambda a=action: self._dispatch(a)
        if not hotkeys:
            return False
        self._manager = ShortcutManager(hotkeys)
        self._manager.start()
        return True
