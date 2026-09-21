"""识别历史：内存态环形队列（v1 不持久化）。"""

import time
from collections import deque
from dataclasses import dataclass

from app.ocr_client import OCRResult


@dataclass
class HistoryEntry:
    timestamp: float
    text: str
    quality: str
    latency_ms: float
    result: OCRResult

    @property
    def preview(self) -> str:
        lines = self.text.splitlines() if self.text else []
        if not lines:
            return "(空)"
        first = lines[0][:24]
        if len(lines) > 1:
            first += f" …+{len(lines) - 1}行"
        return first


class History:
    def __init__(self, max_size: int = 20):
        self._entries: deque[HistoryEntry] = deque(maxlen=max_size)

    def add(self, result: OCRResult) -> HistoryEntry:
        entry = HistoryEntry(
            timestamp=time.time(),
            text=result.text,
            quality=result.quality,
            latency_ms=result.latency_ms,
            result=result,
        )
        self._entries.appendleft(entry)
        return entry

    def entries(self) -> list[HistoryEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
