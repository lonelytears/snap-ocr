"""历史环形队列测试。"""

from app.history import History
from app.ocr_client import OCRResult


def _result(text: str) -> OCRResult:
    return OCRResult(text=text, quality="fast", latency_ms=80.0, avg_score=0.99)


def test_add_and_order():
    h = History(max_size=3)
    h.add(_result("第一条"))
    h.add(_result("第二条"))
    entries = h.entries()
    assert len(h) == 2
    assert entries[0].text == "第二条"  # 最新在前
    assert entries[0].preview == "第二条"


def test_ring_eviction():
    h = History(max_size=2)
    for i in range(5):
        h.add(_result(f"条目{i}"))
    assert len(h) == 2
    assert h.entries()[0].text == "条目4"
    assert h.entries()[1].text == "条目3"


def test_preview_truncates_and_multiline():
    h = History()
    h.add(_result("很长很长很长很长很长很长很长很长很长的一行\n第二行"))
    preview = h.entries()[0].preview
    # 首行截断 24 字 + 多行时附 "…+N行" 后缀
    assert preview.startswith("很长" * 2)
    assert preview.endswith("…+1行")


def test_preview_single_line_no_suffix():
    h = History()
    h.add(_result("只有一行"))
    assert h.entries()[0].preview == "只有一行"
