"""热键体系（M2+）：单监听器 + 原地换绑。

崩溃教训（2026-09-21）：改设置后 stop 旧监听再 start 新监听，pynput 在
macOS 上销毁/重建 CGEventTap 与主线程 CFRunLoop 竞态，进程直接被杀
（无 Python 异常、无 atexit）——表现为"保存后软件自动关闭"。

因此：监听器（及其事件 tap）全生命周期只创建一次；换绑 = 原地替换
GlobalHotKeys 的热键表（纯 Python list 引用替换，GIL 下原子）。

线程模型：HotKey 回调发生在 pynput 监听线程，直接触碰 Qt 对象是未定义
行为——经 Qt Signal(object) 桥接（跨线程 emit 自动 QueuedConnection
回主线程，载荷是 callable，主线程侧统一 invoke）。

权限：macOS 需「辅助功能」。AXIsProcessTrusted 只读预检不弹窗。
"""

from PySide6.QtCore import QObject, Signal

_STATUS_LOG = None   # Path，惰性解析（避免模块导入期碰 HOME）


def log_hotkey_status(event: str, ok: bool, detail: str = "") -> None:
    """热键状态落盘（~/Library/Logs/snap-ocr-hotkey.log）——窗口化应用
    stdout 不可见，排障只能靠这个通道。"""
    global _STATUS_LOG
    try:
        from datetime import datetime
        from pathlib import Path

        if _STATUS_LOG is None:
            _STATUS_LOG = Path.home() / "Library" / "Logs" / "snap-ocr-hotkey.log"
        _STATUS_LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%F %T")
        with _STATUS_LOG.open("a") as f:
            f.write(f"{stamp} {event} ok={ok} ax={has_ax_permission()} {detail}\n")
    except OSError:
        pass


def has_ax_permission() -> bool:
    """只读预检键盘监听权限（不弹窗）。

    pynput 用 listen-only 事件 tap：这类 tap 依据的是「输入监控」
    (ListenEvent) 权限；辅助功能(Accessibility) 是抑制类 tap 的要求、
    也是常见误判源——两者任一满足即可。实际可用性最终以 rebuild()
    里「事件 tap 是否真的建立」为准。

    历史坑：AXIsProcessTrusted 住在 HIServices，Quartz 不导出它——曾误用
    Quartz 符号 + 异常吞掉恒返回 True；后又一刀切只认辅助功能，把已授
    输入监控的可用路径也堵死（热键"保存成功但不生效"的真因之一）。
    """
    try:
        import Quartz

        if Quartz.CGPreflightListenEventAccess():   # 输入监控（listen-only tap 的正主）
            return True
    except (ImportError, AttributeError):
        pass
    try:
        from HIServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())           # 辅助功能兜底
    except ImportError:
        return True   # 非 macOS/无绑定时按可用处理，不阻断


def validate_hotkey(hotkey_str: str) -> bool:
    """纯函数便于单测：热键串能否被 pynput 解析。"""
    from pynput import keyboard

    try:
        keyboard.HotKey.parse(hotkey_str)
        return True
    except (ValueError, KeyError):
        return False


class _HotkeyBridge(QObject):
    """pynput 线程 → Qt 主线程的唯一通道。"""

    triggered = Signal(object)   # 载荷为 callable

    def __init__(self):
        super().__init__()
        self.triggered.connect(lambda fn: fn())


class RebindableHotkeys:
    """包一层 GlobalHotKeys：暴露安全的原地换绑。

    只依赖 GlobalHotKeys 的稳定内部结构 self._hotkeys（list of HotKey，
    _on_press/_on_release 每次事件遍历它）——监听器永不停启。
    """

    def __init__(self, hotkeys: dict[str, object]):
        if not hotkeys:
            raise ValueError("hotkeys 不能为空")
        from pynput import keyboard

        self._kb = keyboard
        self._listener = keyboard.GlobalHotKeys(hotkeys)

    def rebind(self, hotkeys: dict[str, object]) -> None:
        """换绑（可空表=全部停用）；监听器与事件 tap 不动。"""
        self._listener._hotkeys = [
            self._kb.HotKey(self._kb.HotKey.parse(key), cb)
            for key, cb in hotkeys.items()
        ]

    def start(self) -> None:
        self._listener.start()

    def raw_listener(self):
        """暴露底层 GlobalHotKeys（供启动后检查 IS_TRUSTED 等状态）。"""
        return self._listener


class HotkeyController:
    """按当前 Settings 装配热键；设置变更后 rebuild() 原地换绑生效。"""

    def __init__(self, action_dispatcher):
        """action_dispatcher(action: str)：热键触发时回调（主线程）。"""
        self._dispatch = action_dispatcher
        self._bridge = _HotkeyBridge()
        self._listener: RebindableHotkeys | None = None

    def rebuild(self) -> bool:
        """（重）装配热键表。返回是否处于可用状态（有权限且有有效热键）。"""
        from app.config import get_settings

        if not has_ax_permission():
            return False
        s = get_settings()
        hotkeys: dict[str, object] = {}
        for key_str, action in ((s.hotkey, "copy"), (s.hotkey_ocr, "ocr")):
            if not key_str or not validate_hotkey(key_str) or key_str in hotkeys:
                continue   # 无效键跳过；两动作撞键保留先者
            hotkeys[key_str] = lambda a=action: self._bridge.triggered.emit(
                lambda: self._dispatch(a)
            )
        if not hotkeys:
            return False
        if self._listener is None:
            self._listener = RebindableHotkeys(hotkeys)
            self._listener.start()   # 唯一一次 start，此后只 rebind
            try:
                self._listener.raw_listener().wait(1.0)
                # 以「事件 tap 是否真的建立」为准（_run 里 tap 创建失败会
                # 提前返回并保持 _loop=None）——不依赖可能误判的预检。
                if getattr(self._listener.raw_listener(), "_loop", None) is None:
                    self._listener = None   # 清引用允许授权后重建
                    return False
            except Exception:  # noqa: BLE001 — wait 失败不影响主流程
                pass
        else:
            self._listener.rebind(hotkeys)
        return True
