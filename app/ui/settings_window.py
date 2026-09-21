"""设置面板（iShot 风格：左侧分类栏 + 右侧白卡片内容页）。

职责纯 UI：编辑值 → 「保存并应用」统一走 app.config.update_user_config
并发射 settings_saved(新 Settings)；热键重装/OCR 客户端重建由订阅方完成。

页面：
  通用   —— OCR 服务地址（可指向远程）+ 连接测试 + 质量档 + 剪贴板比例
  快捷键 —— 截图 / 截图并识别 两组录入控件（点击后按组合键，冲突标红）
  大模型 —— 预留三项（下版本「截图翻译」使用，当前仅存储）
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.config import Settings, reset_user_config, update_user_config
from app.hotkey import ShortcutManager

# ── Qt 键事件 → pynput 热键串 的映射表（纯数据；顺序 = macOS 惯例 ⌃⌥⇧⌘）──
MOD_MAP = [
    (Qt.KeyboardModifier.ControlModifier, "<ctrl>", "⌃"),
    (Qt.KeyboardModifier.AltModifier, "<alt>", "⌥"),
    (Qt.KeyboardModifier.ShiftModifier, "<shift>", "⇧"),
    (Qt.KeyboardModifier.MetaModifier, "<cmd>", "⌘"),
]

# 修饰键本身的 key code（录入时按下纯修饰不算有效组合）
_MOD_KEY_CODES = {
    Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock, Qt.Key.Key_NumLock,
}


def qt_event_to_pynput(key, modifiers) -> str | None:
    """纯函数便于单测：Qt key+modifiers → pynput 串；无修饰或纯修饰返回 None。"""
    if key in _MOD_KEY_CODES:
        return None
    parts = [tag for mod, tag, _ in MOD_MAP if modifiers & mod]
    if not parts:
        return None
    text = chr(key) if Qt.Key.Key_A <= key <= Qt.Key.Key_Z else None
    if text is None:
        return None
    return "+".join(parts + [text.lower()])


def pynput_to_display(hotkey_str: str) -> str:
    """'<alt>+<shift>+o' → '⌥⇧O'（无修饰原样小写展示）。"""
    symbols = {tag: sym for _, tag, sym in MOD_MAP}
    out = []
    for token in hotkey_str.split("+"):
        if token in symbols:
            out.append(symbols[token])
        else:
            out.append(token.upper() if len(token) == 1 else token)
    return "".join(out)


class HotkeyEdit(QPushButton):
    """快捷键录入按钮：点击进入录入态 → 按下组合键捕获；Backspace 清除。"""

    changed = Signal()

    def __init__(self, hotkey_str: str, parent=None):
        super().__init__(parent)
        self._value = hotkey_str
        self._capturing = False
        self.setMinimumHeight(30)
        self.setMinimumWidth(130)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh()
        self.clicked.connect(lambda _=False: self._enter_capture())

    def value(self) -> str:
        return self._value

    def set_value(self, hotkey_str: str) -> None:
        self._value = hotkey_str
        self._refresh()

    def _refresh(self) -> None:
        if self._capturing:
            self.setText("按下组合键…")
            self.setStyleSheet("color:#2563eb; border:1px solid #2563eb; border-radius:6px;")
        else:
            self.setText(pynput_to_display(self._value) if self._value else "未设置")
            self.setStyleSheet(
                "text-align:left; padding-left:8px; border:1px solid #d1d5db;"
                " border-radius:6px; background:#ffffff;"
            )

    def _enter_capture(self) -> None:
        self._capturing = True
        self.grabKeyboard()
        self._refresh()

    def _exit_capture(self) -> None:
        self._capturing = False
        self.releaseKeyboard()
        self._refresh()

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Escape:
            self._exit_capture()
            return
        if event.key() == Qt.Key.Key_Backspace:
            self._value = ""
            self.changed.emit()
            self._exit_capture()
            return
        parsed = qt_event_to_pynput(event.key(), event.modifiers())
        if parsed and ShortcutManager.validate(parsed):
            self._value = parsed
            self.changed.emit()
            self._exit_capture()


def _card(page: QWidget) -> QFrame:
    frame = QFrame(page)
    frame.setStyleSheet(
        "QFrame { background:#ffffff; border-radius:12px; }"
        "QLabel { color:#374151; font-size:13px; }"
        "QLineEdit, QComboBox { border:1px solid #d1d5db; border-radius:6px;"
        " padding:4px 8px; background:#ffffff; font-size:13px; min-height:22px; }"
    )
    return frame


class SettingsWindow(QWidget):
    """单实例设置窗（托盘持有引用）。"""

    settings_saved = Signal(object)   # 新 Settings

    def __init__(self, settings: Settings):
        super().__init__()
        self._init_values = settings
        self.setWindowTitle("snap-ocr 设置")
        self.resize(680, 440)
        self.setStyleSheet("SettingsWindow { background:#ececec; }")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 左：分类栏（iShot 式图标行）──
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(172)
        self.sidebar.setStyleSheet(
            "QListWidget { background:transparent; border:none; outline:none;"
            " font-size:13px; }"
            "QListWidget::item { height:40px; border-radius:8px; margin:4px 8px;"
            " padding-left:14px; color:#374151; }"
            "QListWidget::item:selected { background:#2563eb; color:#ffffff; }"
        )
        pages = [("⚙  通用", self._build_general_page),
                 ("⌨  快捷键", self._build_hotkey_page),
                 ("🤖  大模型", self._build_llm_page)]
        self.stack = QStackedWidget()
        for title, builder in pages:
            QListWidgetItem(title, self.sidebar)
            self.stack.addWidget(builder())
        self.sidebar.setCurrentRow(0)
        self.stack.setCurrentIndex(0)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)

        # ── 右：内容页 + 底部保存条 ──
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addWidget(self.stack, stretch=1)
        bar = QWidget()
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(16, 8, 16, 12)
        reset_btn = QPushButton("恢复默认")
        reset_btn.setStyleSheet(
            "color:#6b7280; border:none; font-size:13px; padding:6px 10px;"
        )
        reset_btn.clicked.connect(lambda _=False: self._reset())
        save_btn = QPushButton("保存并应用")
        save_btn.setStyleSheet(
            "background:#2563eb; color:#ffffff; border-radius:6px;"
            " padding:6px 18px; font-size:13px;"
        )
        save_btn.clicked.connect(lambda _=False: self._save())
        self._save_btn = save_btn
        bar_lay.addStretch(1)
        bar_lay.addWidget(reset_btn)
        bar_lay.addWidget(save_btn)
        right_lay.addWidget(bar)

        root.addWidget(self.sidebar)
        root.addWidget(right, stretch=1)

    # ── 页面构建 ──────────────────────────
    def _page_wrapper(self, body: QWidget) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(16, 16, 16, 16)
        card = _card(page)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 18, 20, 18)
        card_lay.addWidget(body)
        lay.addWidget(card, stretch=1)
        return page

    def _build_general_page(self) -> QWidget:
        s = self._init_values
        body = QWidget()
        form = QFormLayout(body)
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        url_row = QHBoxLayout()
        self.ocr_url_edit = QLineEdit(s.ocr_base_url)
        test_btn = QPushButton("测试")
        test_btn.setStyleSheet(
            "border:1px solid #2563eb; color:#2563eb; border-radius:6px;"
            " padding:4px 12px; background:#ffffff;"
        )
        test_btn.clicked.connect(lambda _=False: self._test_ocr())
        self.ocr_status = QLabel("")
        self.ocr_status.setStyleSheet("font-size:12px;")
        url_row.addWidget(self.ocr_url_edit)
        url_row.addWidget(test_btn)
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.addLayout(url_row)
        col.addWidget(self.ocr_status)
        form.addRow("OCR 服务地址", holder)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["fast（~80ms）", "accurate（~380ms）"])
        self.quality_combo.setCurrentIndex(0 if s.ocr_quality == "fast" else 1)
        form.addRow("识别质量档", self.quality_combo)

        self.clipboard_combo = QComboBox()
        self.clipboard_combo.addItems(["logical（与框选 1:1）", "device（Retina 2x 物理）"])
        self.clipboard_combo.setCurrentIndex(
            0 if s.clipboard_scale == "logical" else 1
        )
        form.addRow("剪贴板输出", self.clipboard_combo)

        tip = QLabel("远程地址示例：http://192.168.1.10:8000（指向运行 ocr-service 的机器）")
        tip.setStyleSheet("color:#9ca3af; font-size:12px;")
        form.addRow("", tip)

        return self._page_wrapper(body)

    def _build_hotkey_page(self) -> QWidget:
        s = self._init_values
        body = QWidget()
        form = QFormLayout(body)
        form.setSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.hk_capture = HotkeyEdit(s.hotkey)
        self.hk_ocr = HotkeyEdit(s.hotkey_ocr)
        for w in (self.hk_capture, self.hk_ocr):
            w.changed.connect(self._check_conflict)
        self.hk_capture_hint = QLabel("进入截图：拖框标注，完成进剪贴板")
        self.hk_ocr_hint = QLabel("截图并识别：完成直接 OCR")
        for hint in (self.hk_capture_hint, self.hk_ocr_hint):
            hint.setStyleSheet("color:#9ca3af; font-size:12px;")
        form.addRow("截图", self._labeled(self.hk_capture, self.hk_capture_hint))
        form.addRow("截图并识别", self._labeled(self.hk_ocr, self.hk_ocr_hint))

        tip = QLabel("点击右侧按钮后按下组合键（至少一个修饰键）；Backspace 清除，Esc 取消录入")
        tip.setStyleSheet("color:#9ca3af; font-size:12px;")
        form.addRow("", tip)
        return self._page_wrapper(body)

    def _build_llm_page(self) -> QWidget:
        s = self._init_values
        body = QWidget()
        form = QFormLayout(body)
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.llm_url_edit = QLineEdit(s.llm_base_url)
        self.llm_url_edit.setPlaceholderText("https://api.example.com/v1")
        form.addRow("Base URL", self.llm_url_edit)

        self.llm_key_edit = QLineEdit(s.llm_api_key)
        self.llm_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_key_edit.setPlaceholderText("sk-…")
        form.addRow("API Key", self.llm_key_edit)

        self.llm_model_edit = QLineEdit(s.llm_model)
        self.llm_model_edit.setPlaceholderText("模型名")
        form.addRow("Model", self.llm_model_edit)

        badge = QLabel("🔒 预留配置 —— 下个版本「截图翻译」启用，当前仅保存不使用")
        badge.setStyleSheet("color:#9ca3af; font-size:12px;")
        form.addRow("", badge)
        return self._page_wrapper(body)

    def _labeled(self, edit: HotkeyEdit, hint: QLabel) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addWidget(edit, alignment=Qt.AlignmentFlag.AlignLeft)
        col.addWidget(hint)
        return holder

    # ── 行为 ─────────────────────────────
    def _check_conflict(self) -> None:
        a, b = self.hk_capture.value(), self.hk_ocr.value()
        conflict = bool(a) and a == b
        color = "#ef4444" if conflict else "#9ca3af"
        self.hk_capture_hint.setText("⚠ 与另一快捷键冲突" if conflict
                                     else "进入截图：拖框标注，完成进剪贴板")
        self.hk_capture_hint.setStyleSheet(f"color:{color}; font-size:12px;")

    def _test_ocr(self) -> None:
        import httpx

        url = self.ocr_url_edit.text().strip().rstrip("/")
        self.ocr_status.setText("连接中…")
        self.ocr_status.setStyleSheet("color:#6b7280; font-size:12px;")
        try:
            resp = httpx.get(f"{url}/healthz", timeout=2.0)
            ok = resp.status_code == 200
        except Exception:  # noqa: BLE001 — 任何网络错误都归为不可达
            ok = False
        if ok:
            self.ocr_status.setText("● 服务正常")
            self.ocr_status.setStyleSheet("color:#10b981; font-size:12px;")
        else:
            self.ocr_status.setText("● 无法连接（服务未启动或地址错误）")
            self.ocr_status.setStyleSheet("color:#ef4444; font-size:12px;")

    def _save(self) -> None:
        if self.hk_capture.value() and self.hk_capture.value() == self.hk_ocr.value():
            self.sidebar.setCurrentRow(1)
            self._check_conflict()
            return
        updates = {
            "ocr_base_url": self.ocr_url_edit.text().strip().rstrip("/"),
            "ocr_quality": "fast" if self.quality_combo.currentIndex() == 0 else "accurate",
            "clipboard_scale": "logical" if self.clipboard_combo.currentIndex() == 0
                               else "device",
            "hotkey": self.hk_capture.value(),
            "hotkey_ocr": self.hk_ocr.value(),
            "llm_base_url": self.llm_url_edit.text().strip(),
            "llm_api_key": self.llm_key_edit.text().strip(),
            "llm_model": self.llm_model_edit.text().strip(),
        }
        try:
            settings = update_user_config(updates)
            self._init_values = settings
            self.settings_saved.emit(settings)
            self._flash_saved()
        except Exception as e:  # noqa: BLE001 — 保存链路任何异常都必须可见
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "保存失败", f"{type(e).__name__}: {e}")

    def _flash_saved(self, btn: QPushButton | None = None) -> None:
        """保存成功的可见反馈：按钮变「✓ 已保存」1.5s 后复原。"""
        target = btn or getattr(self, "_save_btn", None)
        if target is None:
            return
        from PySide6.QtCore import QTimer

        original = target.text()
        target.setText("✓ 已保存")
        target.setStyleSheet(
            "background:#10b981; color:#ffffff; border-radius:6px;"
            " padding:6px 18px; font-size:13px;"
        )
        target.setEnabled(False)
        QTimer.singleShot(1500, lambda: (
            target.setText(original),
            target.setStyleSheet(
                "background:#2563eb; color:#ffffff; border-radius:6px;"
                " padding:6px 18px; font-size:13px;"
            ),
            target.setEnabled(True),
        ))

    def _reset(self) -> None:
        settings = reset_user_config()
        self._init_values = settings
        self.ocr_url_edit.setText(settings.ocr_base_url)
        self.quality_combo.setCurrentIndex(0)
        self.clipboard_combo.setCurrentIndex(0)
        self.hk_capture.set_value(settings.hotkey)
        self.hk_ocr.set_value(settings.hotkey_ocr)
        self.llm_url_edit.setText("")
        self.llm_key_edit.setText("")
        self.llm_model_edit.setText("")
        self._check_conflict()
        self.settings_saved.emit(settings)
        self._flash_saved()
