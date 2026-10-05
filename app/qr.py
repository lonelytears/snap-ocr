"""本地二维码解码（纯函数层，永不抛异常）。

zxing-cpp>=3.1 原生支持 QImage 输入，无需 Pillow/numpy。已知兼容性坑：
zxing 绑定对非直通格式内部调 convertToFormat(24)（int 字面量），PySide6
严格类型检查直接 TypeError——所以这里**统一先自转 Grayscale8**（QR 检测
靠二值化，灰度无损），彻底绕开该分支。

降级策略：缺库/坏图/解码异常一律返回 []（=未命中，照走 OCR）——识别
功能的可用性优先于二维码功能本身。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ocr_client import OCRResult


@dataclass(frozen=True)
class QRHit:
    text: str
    format: str  # 展示/未来一维码扩展用，如 "QR Code"


def decode_image(image, formats=None) -> list[QRHit]:
    """QImage → 全部二维码命中。任何失败都视为未命中（返回 []）。"""
    try:
        import zxingcpp
        from PySide6.QtGui import QImage

        if image.format() != QImage.Format.Format_Grayscale8:
            image = image.convertToFormat(QImage.Format.Format_Grayscale8)
        # formats 收窄到 QRCode：一维码检出器容易把截图里的表格线/虚线
        # 读成垃圾码，一旦误命中会静默劫持 OCR 输出
        if formats is None:
            formats = zxingcpp.BarcodeFormat.QRCode
        barcodes = zxingcpp.read_barcodes(image, formats=formats)
        return [QRHit(b.text, getattr(b.format, "name", str(b.format)))
                for b in barcodes if b.text]
    except ImportError:
        return []  # 缺库降级：纯 OCR，功能不炸
    except Exception:  # noqa: BLE001 — 坏图/绑定异常都按未命中处理
        return []


def decode_file(path: str) -> list[QRHit]:
    """磁盘图片路径 → 命中列表。QImage 加载线程安全，可在 QThread 用。"""
    from PySide6.QtGui import QImage

    img = QImage(path)
    if img.isNull():
        return []
    return decode_image(img)


def to_result(hits: list[QRHit], latency_ms: float = 0.0) -> OCRResult:
    """命中 → OCRResult（kind="qr"），多码空行分隔（不污染复制内容）。"""
    return OCRResult(
        text="\n\n".join(h.text for h in hits),
        blocks=[],
        latency_ms=latency_ms,
        avg_score=1.0,
        quality="qr",
        kind="qr",
    )


_URL_LINE_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def extract_first_url(text: str) -> str | None:
    """逐行找第一个整行 URL。二维码主用例全文一行；OCR 混排文本同样适用。"""
    for line in text.splitlines():
        stripped = line.strip()
        if _URL_LINE_RE.match(stripped):
            return stripped
    return None
