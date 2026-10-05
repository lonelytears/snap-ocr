"""识别 worker 三分支测试：QR 命中短路 / miss 回退 OCR / OCR 失败。

直调 worker.run()（同线程执行，不进事件循环）+ lambda 收信号——
沿用项目"逻辑驱动不 show"的测试惯例。
"""

from pathlib import Path

from PySide6.QtGui import QImage

from app.ocr_client import OCRResult, OCRServiceUnavailable
from app.ui.tray import _RecognizeWorker

FIX = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.call_count = 0

    def recognize_file(self, path, quality="fast"):
        self.call_count += 1
        if self._error:
            raise self._error
        return self._result


def _plain_image(tmp_path) -> str:
    img = QImage(100, 60, QImage.Format.Format_RGB32)
    img.fill(0xFFFFFFFF)
    p = tmp_path / "plain.png"
    img.save(str(p))
    return str(p)


def _run(worker):
    got, failed = [], []
    worker.finished_ok.connect(lambda r: got.append(r))
    worker.failed.connect(lambda m: failed.append(m))
    worker.run()          # 同步执行线程体
    return got, failed


def test_qr_hit_short_circuits_remote():
    """二维码命中：emit kind=qr 且完全不调远程 OCR。"""
    client = FakeClient(result=OCRResult(text="不该出现"))
    worker = _RecognizeWorker(client, str(FIX / "qr_url.png"), "fast")
    got, failed = _run(worker)
    assert not failed
    assert len(got) == 1
    assert got[0].kind == "qr"
    assert got[0].text == "https://example.com/snap-ocr"
    assert client.call_count == 0          # 短路验证


def test_qr_miss_falls_back_to_ocr(tmp_path):
    """无码图片：照走远程 OCR，结果 kind="ocr"。"""
    expected = OCRResult(text="普通文字")
    client = FakeClient(result=expected)
    worker = _RecognizeWorker(client, _plain_image(tmp_path), "fast")
    got, failed = _run(worker)
    assert not failed
    assert got == [expected]
    assert got[0].kind == "ocr"
    assert client.call_count == 1


def test_ocr_failure_emits_failed(tmp_path):
    client = FakeClient(error=OCRServiceUnavailable("服务未启动"))
    worker = _RecognizeWorker(client, _plain_image(tmp_path), "fast")
    got, failed = _run(worker)
    assert not got
    assert failed == ["服务未启动"]
