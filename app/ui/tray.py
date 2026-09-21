"""托盘：菜单入口 + 识别流程编排（截图 → 识别 → 浮窗/通知）+ 历史回看。"""

import os
import tempfile
import time

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from app.capture.screencapture import CaptureCancelled
from app.config import Settings
from app.history import History, HistoryEntry
from app.ocr_client import OCRClient, OCRResult, OCRServiceUnavailable
from app.ui.result_window import PinnedResultWindow, ResultWindow


def _make_icon() -> QIcon:
    """程序化画一个简洁的『s』图标，免维护图片资源。"""
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen()
    pen.setStyle(Qt.PenStyle.NoPen)
    p.setPen(pen)
    p.setBrush(QColor("#2563eb"))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("#ffffff"))
    font = p.font()
    font.setPixelSize(34)
    font.setBold(True)
    p.setFont(font)
    p.drawText(pm.rect(), int(Qt.AlignmentFlag.AlignCenter), "s")
    p.end()
    return QIcon(pm)


class _RecognizeWorker(QThread):
    """识别在线程里跑，避免阻塞 Qt 主事件循环（accurate 档可能 2s+）。"""

    finished_ok = Signal(object)      # OCRResult
    failed = Signal(str)

    def __init__(self, client: OCRClient, path: str, quality: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._path = path
        self._quality = quality

    def run(self) -> None:
        try:
            result = self._client.recognize_file(self._path, quality=self._quality)
            self.finished_ok.emit(result)
        except OCRServiceUnavailable as e:
            self.failed.emit(str(e))


class TrayApp(QSystemTrayIcon):
    def __init__(self, settings: Settings, client: OCRClient, fallback):
        super().__init__(_make_icon())
        self._settings = settings
        self._client = client
        self._fallback = fallback    # 系统截图回退(免权限)
        self._warned_permission = False
        self._history = History(settings.history_size)
        self._quality = settings.ocr_quality
        self._worker: _RecognizeWorker | None = None
        self._busy = False  # 防重入：截图会话/识别进行中忽略新触发

        self.setToolTip("snap-ocr 截图取字")
        self._build_menu()

        self.activated.connect(self._on_activated)
        service_ok = client.health()
        if not service_ok:
            self.showMessage(
                "snap-ocr",
                f"OCR 服务不可达: {settings.ocr_base_url}\n"
                "请先启动 ocr-service（:8000）",
                QSystemTrayIcon.MessageIcon.Warning,
            )

    # ── 菜单 ──────────────────────────────
    def _build_menu(self) -> None:
        menu = QMenu()

        act = QAction("截图识别", menu)
        act.triggered.connect(self.start_flow)
        menu.addAction(act)

        # 质量档切换
        quality_menu = menu.addMenu("质量档")
        self._quality_actions: dict[str, QAction] = {}
        for q, label in (("fast", "fast（~80ms）"), ("accurate", "accurate（~380ms）")):
            qa = QAction(label, quality_menu, checkable=True)
            qa.triggered.connect(lambda checked, q=q: self._set_quality(q))
            quality_menu.addAction(qa)
            self._quality_actions[q] = qa
        self._sync_quality_menu()
        menu.addMenu(quality_menu)

        # 历史
        history_menu = menu.addMenu("历史")
        history_menu.aboutToShow.connect(
            lambda: self._rebuild_history_menu(history_menu)
        )
        menu.addMenu(history_menu)

        menu.addSeparator()
        quit_act = QAction("退出", menu)
        quit_act.triggered.connect(self._quit)
        menu.addAction(quit_act)

        self.setContextMenu(menu)

    def _sync_quality_menu(self) -> None:
        for q, act in self._quality_actions.items():
            act.setChecked(q == self._quality)

    def _set_quality(self, q: str) -> None:
        self._quality = q
        self._sync_quality_menu()

    def _rebuild_history_menu(self, menu: QMenu) -> None:
        menu.clear()
        entries = self._history.entries()
        if not entries:
            empty = QAction("(空)", menu)
            empty.setEnabled(False)
            menu.addAction(empty)
            return
        for e in entries:
            time_str = time.strftime("%H:%M:%S", time.localtime(e.timestamp))
            act = QAction(f"{time_str}  {e.preview}", menu)
            act.triggered.connect(lambda _=False, e=e: self._show_entry(e))
            menu.addAction(act)

    def _show_entry(self, entry: HistoryEntry) -> None:
        ResultWindow(entry, self._settings.result_font_pt)

    # ── 识别流程 ──────────────────────────
    def _on_activated(self, reason) -> None:
        # 单击托盘图标 = 快捷触发（Mac 上 Trigger 只有点左键）
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.start_flow()

    def _pick_backend(self):
        """V2 会话模式优先；无屏幕录制权限时回退系统截图。"""
        from app.screenshot.session import ScreenshotManager, has_screen_permission

        if self._settings.capture_backend != "overlay":
            return None
        if has_screen_permission():
            return ScreenshotManager()
        if not self._warned_permission:
            self._warned_permission = True
            self.showMessage(
                "截图会话需要屏幕录制权限",
                "系统设置 → 隐私与安全性 → 屏幕录制 → 勾选运行本程序的终端/App 后重启。\n"
                "本次已回退为系统截图模式。",
                QSystemTrayIcon.MessageIcon.Warning,
            )
        return None

    def start_flow(self) -> None:
        if self._busy:
            return
        self._busy = True
        manager = self._pick_backend()

        if manager is not None:
            # ── V2: Screenshot Session（完成动作按 SessionResult.action 分发）──
            try:
                result = manager.start_session()
            except Exception as e:  # noqa: BLE001 — 会话层异常要可见
                QMessageBox.warning(None, "snap-ocr", f"截图失败: {e}")
                return
            finally:
                self._busy = False
            if result.cancelled or result.image is None:
                return

            if result.action == "ocr":
                self._start_ocr(result.image)      # busy 由识别 worker 收尾
                return
            if result.action == "save":
                self._save_image(result.image)
                return

            # ── copy：进剪贴板 + 预览 ──
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QGuiApplication

            image = result.image
            if self._settings.clipboard_scale == "logical" and image.devicePixelRatio() > 1:
                # 按逻辑尺寸输出：贴图与框选区域 1:1，消除 Retina 2x 的视觉歧义
                dpr = image.devicePixelRatio()
                image = image.scaled(
                    int(image.width() / dpr), int(image.height() / dpr),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            QGuiApplication.clipboard().setImage(image)
            # 结果预览窗：当场对照「框的」vs「得到的」，不经过剪贴板与记忆
            image.save("/tmp/snap_result_preview.png")
            from time import time as _time

            from app.history import HistoryEntry
            from app.ocr_client import OCRResult
            from app.ui.result_window import PinnedResultWindow

            preview_result = OCRResult(
                text="(截图完成)", quality="snap",
                latency_ms=float(image.width()), avg_score=0.0,
            )
            entry = HistoryEntry(
                timestamp=_time(), text="(截图完成)", quality="snap",
                latency_ms=float(image.width()), result=preview_result,
            )
            self._preview = PinnedResultWindow(
                "/tmp/snap_result_preview.png", self._settings.result_font_pt
            )
            self._preview.set_result(entry)
            self.showMessage("snap-ocr", "截图已复制到剪贴板",
                             QSystemTrayIcon.MessageIcon.Information)
            return

        # ── 回退：系统交互截图 + OCR（旧链路保留）──
        try:
            outcome = self._fallback.capture()
        except CaptureCancelled:
            self._busy = False
            return  # Esc：静默结束
        except Exception as e:  # noqa: BLE001 — 截图层异常要可见
            self._busy = False
            QMessageBox.warning(None, "snap-ocr", f"截图失败: {e}")
            return

        if outcome is None:
            self._busy = False
            return
        if not outcome.wants_ocr:
            self._busy = False
            if outcome.saved_to:
                self.showMessage("已保存", outcome.saved_to,
                                 QSystemTrayIcon.MessageIcon.Information)
            return

        self._pin = PinnedResultWindow(outcome.image_path, self._settings.result_font_pt)
        self._worker = _RecognizeWorker(self._client, outcome.image_path, self._quality)
        self._worker.finished_ok.connect(self._on_result)
        self._worker.failed.connect(self._on_failure)
        self._worker.finished.connect(lambda: setattr(self, "_busy", False))
        self._worker.start()

    def _start_ocr(self, image) -> None:
        """V2 会话的 OCR 动作：临时落盘 → 贴图窗 + 识别 worker（点按才调接口）。"""
        self._busy = True
        path = os.path.join(tempfile.gettempdir(), f"snap_{int(time.time() * 1000)}.png")
        image.save(path)
        self._pin = PinnedResultWindow(path, self._settings.result_font_pt)
        self._worker = _RecognizeWorker(self._client, path, self._quality)
        self._worker.finished_ok.connect(self._on_result)
        self._worker.failed.connect(self._on_failure)
        self._worker.finished.connect(lambda: setattr(self, "_busy", False))
        self._worker.start()

    def _save_image(self, image) -> None:
        """V2 会话的保存动作：系统另存对话框 → PNG 落盘。"""
        from PySide6.QtWidgets import QFileDialog

        default = os.path.expanduser(f"~/Desktop/snap_{int(time.time() * 1000)}.png")
        path, _ = QFileDialog.getSaveFileName(None, "保存截图", default, "PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if image.save(path):
            self.showMessage("已保存", path, QSystemTrayIcon.MessageIcon.Information)
        else:
            self.showMessage("保存失败", path, QSystemTrayIcon.MessageIcon.Warning)

    def _on_result(self, result: OCRResult) -> None:
        entry = self._history.add(result)
        self._safe_pin(lambda pin: pin.set_result(entry))

    def _on_failure(self, message: str) -> None:
        self._safe_pin(lambda pin: pin.set_error(message))

    def _safe_pin(self, fn) -> None:
        """贴图窗可能已被用户关掉——对已销毁的 C++ 对象操作会抛 RuntimeError。"""
        pin = getattr(self, "_pin", None)
        if pin is None:
            return
        try:
            fn(pin)
        except RuntimeError:
            pass  # 窗口已关闭，忽略

    def _quit(self) -> None:
        self._client.close()
        QApplication.quit()
