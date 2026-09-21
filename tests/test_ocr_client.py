"""OCR 客户端测试：mock 传输层，覆盖成功/连接失败/超时/非200。"""

import httpx
import pytest

from app.ocr_client import OCRClient, OCRServiceUnavailable


def _client(handler) -> OCRClient:
    transport = httpx.MockTransport(handler)
    c = OCRClient.__new__(OCRClient)
    c._base = "http://127.0.0.1:8000"
    c._timeout = 3.0
    c._client = httpx.Client(timeout=3.0, transport=transport)
    return c


def test_recognize_success(tmp_path):
    img = tmp_path / "snap.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "quality=fast" in str(request.url)
        return httpx.Response(200, json={
            "text": "你好世界",
            "blocks": [{"text": "你好世界", "score": 0.98, "box": [[0, 0]] * 4}],
            "meta": {"latency_ms": 79.0},
        })

    result = _client(handler).recognize_file(str(img), quality="fast")
    assert result.text == "你好世界"
    assert result.avg_score == 0.98
    assert result.latency_ms == 79.0


def test_recognize_connect_error(tmp_path):
    img = tmp_path / "s.png"
    img.write_bytes(b"x")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(OCRServiceUnavailable, match="未启动"):
        _client(handler).recognize_file(str(img))


def test_recognize_timeout(tmp_path):
    img = tmp_path / "s.png"
    img.write_bytes(b"x")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    with pytest.raises(OCRServiceUnavailable, match="超时"):
        _client(handler).recognize_file(str(img))


def test_recognize_error_body(tmp_path):
    img = tmp_path / "s.png"
    img.write_bytes(b"x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(415, json={"error": {"code": "UNSUPPORTED", "message": "类型不对"}})

    with pytest.raises(OCRServiceUnavailable, match="类型不对"):
        _client(handler).recognize_file(str(img))


def test_health_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/healthz"
        return httpx.Response(200, json={"status": "ok"})

    assert _client(handler).health() is True
