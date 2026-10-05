"""二维码解码纯函数测试：fixture 命中/阴性/降级/结果转换/URL 提取。

无需 QApplication——QImage 解码不依赖事件循环。
"""

from pathlib import Path

from PySide6.QtGui import QImage

from app.ocr_client import OCRResult
from app.qr import QRHit, decode_file, decode_image, extract_first_url, to_result

FIX = Path(__file__).parent / "fixtures"


def test_decode_url_qr():
    hits = decode_file(str(FIX / "qr_url.png"))
    assert [h.text for h in hits] == ["https://example.com/snap-ocr"]
    assert hits[0].format == "QRCode"


def test_decode_two_qr_codes():
    hits = decode_file(str(FIX / "qr_two.png"))
    texts = {h.text for h in hits}
    assert texts == {"https://example.com/first", "hello-second"}


def test_decode_plain_image_returns_empty(tmp_path):
    img = QImage(200, 100, QImage.Format.Format_RGB32)
    img.fill(0xFFFFFFFF)
    path = tmp_path / "plain.png"
    img.save(str(path))
    assert decode_file(str(path)) == []


def test_decode_missing_file_returns_empty(tmp_path):
    assert decode_file(str(tmp_path / "nope.png")) == []


def test_decode_image_exception_swallowed(tmp_path, monkeypatch):
    """解码器内部异常必须吞成 []（识别可用性优先于二维码功能）。"""

    def boom(*a, **k):
        raise RuntimeError("boom")

    plain = QImage(10, 10, QImage.Format.Format_RGB32)
    monkeypatch.setattr("zxingcpp.read_barcodes", boom)
    assert decode_image(plain) == []


def test_decode_image_missing_library(monkeypatch):
    """缺库降级：返回 [] 不抛（老安装包升级场景）。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "zxingcpp":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert decode_image(QImage(10, 10, QImage.Format.Format_Grayscale8)) == []


def test_to_result():
    r = to_result([QRHit("https://a.b/c", "QRCode"), QRHit("second", "QRCode")], 12.5)
    assert isinstance(r, OCRResult)
    assert r.kind == "qr"
    assert r.quality == "qr"
    assert r.text == "https://a.b/c\n\nsecond"   # 多码空行分隔
    assert r.latency_ms == 12.5


def test_extract_first_url():
    assert extract_first_url("https://example.com/x?y=1") == "https://example.com/x?y=1"
    assert extract_first_url("标题行\nHTTPS://EXAMPLE.COM/x\n正文") == "HTTPS://EXAMPLE.COM/x"
    assert extract_first_url("访问 https://a.b 前先看说明") is None   # 整行才匹配
    assert extract_first_url("没有任何链接\nplain text") is None
