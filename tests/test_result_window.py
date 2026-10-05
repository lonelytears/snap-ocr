"""结果窗测试：QR/OCR 双分支 meta、打开链接按钮可见性、点击打开（patch）。

构造真实 PinnedResultWindow（贴图窗会 show——沿用 test_update_dialog 的
show 驱动方式），openUrl 必须 monkeypatch 否则真开浏览器。
"""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from app.history import History
from app.ocr_client import OCRResult
from app.ui.result_window import PinnedResultWindow

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def qapp_module():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pinned(qapp_module):
    win = PinnedResultWindow(str(FIX / "qr_url.png"), 14)
    yield win
    win.close()


def _entry(result: OCRResult):
    return History(4).add(result)


def test_qr_result_meta_and_open_btn(qapp_module, pinned):
    pinned.set_result(_entry(OCRResult(text="https://example.com/x", quality="qr", kind="qr")))
    assert "二维码" in pinned.meta_label.text()
    assert pinned.open_btn.isVisible()      # show() 过的窗口，isVisible 有效


def test_qr_plain_text_hides_open_btn(qapp_module, pinned):
    pinned.set_result(_entry(OCRResult(text=" WIFI:T:WPA;S:ssid;P:pass;; ", quality="qr",
                                       kind="qr")))
    assert "二维码" in pinned.meta_label.text()
    assert not pinned.open_btn.isVisibleTo(pinned)   # 窗口可见但按钮隐藏


def test_ocr_result_keeps_meta_format(qapp_module, pinned):
    pinned.set_result(_entry(OCRResult(text="普通文字", avg_score=0.97)))
    assert "置信度" in pinned.meta_label.text()
    assert "二维码" not in pinned.meta_label.text()
    assert not pinned.open_btn.isVisibleTo(pinned)


def test_ocr_text_with_url_shows_open_btn(qapp_module, pinned):
    """白赚能力：OCR 文本整行 URL 也给「打开链接」。"""
    r = OCRResult(text="看这个\nhttps://docs.example.com/a\n完", avg_score=0.9)
    pinned.set_result(_entry(r))
    assert pinned.open_btn.isVisibleTo(pinned)


def test_open_btn_click_opens_url(qapp_module, pinned, monkeypatch):
    opened = []

    class _FakeDesktop:
        @staticmethod
        def openUrl(url):
            opened.append(url.toString())

    monkeypatch.setattr("app.ui.result_window.QDesktopServices", _FakeDesktop)
    pinned.set_result(_entry(OCRResult(text="https://example.com/click", quality="qr", kind="qr")))
    pinned.open_btn.click()
    assert opened == ["https://example.com/click"]


def test_error_hides_open_btn(qapp_module, pinned):
    pinned.set_result(_entry(OCRResult(text="https://example.com/x", quality="qr", kind="qr")))
    pinned.set_error("服务未启动")
    assert not pinned.open_btn.isVisibleTo(pinned)
