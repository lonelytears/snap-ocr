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
    settings_reloaded = Signal(object)   # 新 Settings（面板保存后；main 用于重装热键）

    def __init__(self, settings: Settings, client: OCRClient, fallback, updater):
        super().__init__(_make_icon())
        self._settings = settings
        self._client = client
        self._fallback = fallback    # 系统截图回退(免权限)
        self._updater = updater
        self._warned_permission = False
        self._history = History(settings.history_size)
        self._quality = settings.ocr_quality
        self._worker: _RecognizeWorker | None = None
        self._busy = False  # 防重入：截图会话/识别进行中忽略新触发
        self._force_pending = False  # 强制更新未处理：gate 主功能
        self._update_dlg = None

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

        self._wire_updater()
        self._apply_update_schedule(settings)

    # ── 更新 ──────────────────────────────
    def _wire_updater(self) -> None:
        from app.updater import UpdateDecision

        u = self._updater
        u.check_done.connect(self._on_update_decision)
        u.manual_uptodate.connect(
            lambda v: self.showMessage("snap-ocr", f"已是最新版本 v{v}",
                                       QSystemTrayIcon.MessageIcon.Information))
        u.check_failed.connect(
            lambda msg: self.showMessage("检查更新失败", msg,
                                         QSystemTrayIcon.MessageIcon.Warning))
        u.download_progress.connect(self._on_update_progress)
        u.download_done.connect(self._on_update_ready)
        u.download_failed.connect(self._on_update_failed)
        self._update_decision_type = UpdateDecision

    def _apply_update_schedule(self, settings) -> None:
        """启动 3s 后首查 + 每 24h 复查（托盘常驻数周，强制语义靠周期检查维持）。"""
        from PySide6.QtCore import QTimer

        timer = getattr(self, "_update_timer", None)
        if timer is not None:
            timer.stop()
        if not settings.update_auto_check:
            return
        QTimer.singleShot(3000, self._updater.check)
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._updater.check)
        self._update_timer.start(24 * 3600 * 1000)

    def _on_update_decision(self, decision, appcast) -> None:
        if decision is self._update_decision_type.UP_TO_DATE:
            return  # 手动触发的提示由 manual_uptodate 信号负责
        self._show_update_dialog(appcast, forced=decision is self._update_decision_type.FORCED)

    def _show_update_dialog(self, appcast, forced: bool) -> None:
        from app.ui.update_dialog import UpdateDialog

        dlg = self._update_dlg
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        if dlg is not None:
            dlg.deleteLater()
        if forced:
            self._force_pending = True
        dlg = UpdateDialog(appcast, forced)
        dlg.update_requested.connect(
            lambda ac=appcast: self._updater.download_and_stage(ac))
        self._update_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_update_progress(self, pct: int) -> None:
        if self._update_dlg is not None:
            self._update_dlg.set_progress(pct)

    def _on_update_ready(self, plan) -> None:
        if plan.mode == "auto":
            if self._update_dlg is not None:
                self._update_dlg.set_installing()
            self._updater.execute(plan)
            QApplication.quit()   # 立即退出，安装脚本等 pid 结束后完成交换
        else:
            if self._update_dlg is not None:
                self._update_dlg.done(0)
            self._updater.execute(plan)   # open -R 在 Finder 中定位
            self.showMessage(
                "请手动安装",
                "此位置无法自动替换（无写权限或非 Applications）。\n"
                "已在 Finder 中定位新版，请拖入「应用程序」替换后重新打开。",
                QSystemTrayIcon.MessageIcon.Information,
            )

    def _on_update_failed(self, message: str) -> None:
        if self._update_dlg is not None:
            self._update_dlg.set_failed(message)
        else:
            self.showMessage("更新失败", message, QSystemTrayIcon.MessageIcon.Warning)

    def _blocked_by_force_update(self) -> bool:
        """强制更新未处理时 gate 主功能，并把弹窗带回前台。"""
        if not self._force_pending:
            return False
        dlg = self._update_dlg
        if dlg is not None:
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        return True

    # ── 菜单 ──────────────────────────────
    def _build_menu(self) -> None:
        menu = QMenu()

        act = QAction("截图识别", menu)
        act.triggered.connect(lambda _=False: self.start_flow())
        menu.addAction(act)

        settings_act = QAction("设置…", menu)
        settings_act.triggered.connect(lambda _=False: self._open_settings())
        menu.addAction(settings_act)

        update_act = QAction("检查更新…", menu)
        update_act.triggered.connect(lambda _=False: self._updater.check(manual=True))
        menu.addAction(update_act)

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

        # 开机自启（M2）
        self._login_act = QAction("开机自启", menu)
        self._login_act.setCheckable(True)
        self._login_act.triggered.connect(self._toggle_login_item)
        menu.addAction(self._login_act)

        menu.addSeparator()
        quit_act = QAction("退出", menu)
        quit_act.triggered.connect(self._quit)
        menu.addAction(quit_act)

        menu.aboutToShow.connect(self._sync_login_item)
        self.setContextMenu(menu)

    def _sync_login_item(self) -> None:
        from app import login_item

        try:
            self._login_act.setChecked(login_item.is_enabled())
        except Exception:  # noqa: BLE001 — 状态读取失败不影响菜单
            pass

    def _toggle_login_item(self, checked: bool) -> None:
        from app import login_item

        try:
            login_item.set_enabled(checked)
        except Exception as e:  # noqa: BLE001 — 注册失败要可见并回滚勾选
            self._login_act.setChecked(not checked)
            self.showMessage("开机自启设置失败", str(e),
                             QSystemTrayIcon.MessageIcon.Warning)
            return
        self._sync_login_item()

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

    # ── 设置面板 ──────────────────────────
    def _open_settings(self) -> None:
        from app.ui.settings_window import SettingsWindow

        if self._blocked_by_force_update():
            return
        win = getattr(self, "_settings_win", None)
        if win is None:
            win = SettingsWindow(self._settings)
            win.settings_saved.connect(self.apply_settings)
            win.check_update_requested.connect(lambda: self._updater.check(manual=True))
            self._settings_win = win
        win.show()
        win.raise_()
        win.activateWindow()

    def apply_settings(self, settings: Settings) -> None:
        """面板保存后热应用：换配置、重建 OCR 客户端；热键由 main 订阅重装。"""
        self._settings = settings
        self._client.close()
        self._client = OCRClient(settings.ocr_base_url, settings.ocr_timeout_s)
        self._quality = settings.ocr_quality
        self._sync_quality_menu()
        self._updater.apply_settings(settings)
        self._apply_update_schedule(settings)
        self.settings_reloaded.emit(settings)

    def start_flow(self, action: str = "copy") -> None:
        if self._blocked_by_force_update():
            return
        if self._busy:
            return
        self._busy = True
        manager = self._pick_backend()

        if manager is not None:
            # ── V2: Screenshot Session（完成动作按 SessionResult.action 分发）──
            try:
                result = manager.start_session(preferred_action=action)
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

            # ── copy：静默进剪贴板（完成手势不放弹窗，只有 OCR 才开结果窗）──
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
