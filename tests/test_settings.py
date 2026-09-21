"""设置面板/配置层单测：JSON 持久化、优先级、键序列转换、preferred_action。"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import app.config
from app import config
from app.config import Settings, get_settings, update_user_config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """每个用例独立 CONFIG_PATH + 清缓存，互不污染。"""
    monkeypatch.setattr(app.config, "CONFIG_PATH", tmp_path / "config.json")
    app.config.get_settings.cache_clear()
    yield
    app.config.get_settings.cache_clear()


def test_user_json_roundtrip():
    s = update_user_config({"ocr_base_url": "http://192.168.1.10:8000",
                            "llm_model": "gpt-x"})
    assert s.ocr_base_url == "http://192.168.1.10:8000"
    assert s.llm_model == "gpt-x"
    # 新进程读取（清缓存模拟）同一路径
    app.config.get_settings.cache_clear()
    assert get_settings().ocr_base_url == "http://192.168.1.10:8000"


def test_user_json_wins_over_env(monkeypatch):
    monkeypatch.setenv("SNAP_OCR_TIMEOUT_S", "5.0")
    assert get_settings().ocr_timeout_s == 5.0      # 未落盘前 env 生效
    s = update_user_config({"ocr_timeout_s": 9.0})  # 面板保存 = 显式意图，优先级最高
    assert s.ocr_timeout_s == 9.0


def test_stale_keys_ignored():
    config.CONFIG_PATH.write_text('{"__old__": 1, "ocr_quality": "accurate"}')
    app.config.get_settings.cache_clear()
    s = get_settings()
    assert s.ocr_quality == "accurate"


def test_default_hotkeys_valid():
    s = Settings()
    from app.hotkey import ShortcutManager

    assert ShortcutManager.validate(s.hotkey)
    assert ShortcutManager.validate(s.hotkey_ocr)
    assert s.hotkey != s.hotkey_ocr


def test_qt_event_to_pynput():
    from app.ui.settings_window import qt_event_to_pynput

    mods = Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier
    assert qt_event_to_pynput(Qt.Key.Key_O, mods) == "<alt>+<shift>+o"
    assert qt_event_to_pynput(Qt.Key.Key_O, Qt.KeyboardModifier.NoModifier) is None
    assert qt_event_to_pynput(Qt.Key.Key_Shift,
                              Qt.KeyboardModifier.ShiftModifier) is None


def test_pynput_to_display():
    from app.ui.settings_window import pynput_to_display

    assert pynput_to_display("<alt>+<shift>+o") == "⌥⇧O"
    assert pynput_to_display("<cmd>+<ctrl>+j") == "⌘⌃J"


def test_session_preferred_action(qapp_module):
    from PySide6.QtCore import QRect, QRectF
    from PySide6.QtGui import QColor, QPixmap

    img = QPixmap(400, 200)
    img.fill(QColor("#808080"))
    img.setDevicePixelRatio(2.0)

    class FakeSnap:   # QApplication 就绪后再造 QPixmap（函数内执行保证顺序）
        image = img
        screen_bounds = QRect(0, 0, 200, 100)
        scale_factor = 2.0

    from app.screenshot.overlay import OverlayWindow
    from app.screenshot.session import ScreenshotSession

    overlay = OverlayWindow(FakeSnap())
    session = ScreenshotSession(overlay, preferred_action="ocr")
    got: dict = {}
    session.finished.connect(lambda r: got.update(result=r))
    session.start()
    overlay.canvas.selection.set_rect(QRectF(0, 0, 150, 60))
    overlay.canvas.double_click_complete.emit()   # 手势完成 → 用 preferred 动作
    assert got["result"].action == "ocr"
    assert not got["result"].cancelled


@pytest.fixture(scope="module")
def qapp_module():
    return QApplication.instance() or QApplication([])
