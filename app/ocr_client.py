"""OCR 服务客户端：识别 + 健康探测 + 精确失败诊断。"""

from dataclasses import dataclass, field

import httpx


@dataclass
class OCRResult:
    text: str
    blocks: list[dict] = field(default_factory=list)
    latency_ms: float = 0.0
    avg_score: float = 0.0
    quality: str = "fast"


class OCRServiceUnavailable(Exception):
    """连接层失败（服务未启动/挂了），带诊断信息。"""

    def __init__(self, message: str, *, healthy_before: bool | None = None):
        super().__init__(message)
        self.healthy_before = healthy_before


class OCRClient:
    def __init__(self, base_url: str, timeout_s: float = 3.0):
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._client = httpx.Client(timeout=timeout_s)

    def health(self) -> bool:
        try:
            return self._client.get(f"{self._base}/healthz").status_code == 200
        except httpx.HTTPError:
            return False

    def recognize_file(self, image_path: str, quality: str = "fast") -> OCRResult:
        try:
            with open(image_path, "rb") as f:
                resp = self._client.post(
                    f"{self._base}/api/v1/ocr",
                    params={"quality": quality},
                    files={"file": ("snap.png", f, "image/png")},
                )
        except httpx.ConnectError as e:
            raise OCRServiceUnavailable(
                f"无法连接 OCR 服务 {self._base}（服务未启动？）"
            ) from e
        except httpx.TimeoutException as e:
            # 超时但端口有响应 → 服务在但慢，提示换 fast 档
            raise OCRServiceUnavailable(
                f"识别超时(>{self._timeout}s)，服务过载或 quality=accurate 过慢"
            ) from e

        if resp.status_code != 200:
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001
                pass
            raise OCRServiceUnavailable(
                f"OCR 服务返回 {resp.status_code}: {detail or resp.text[:120]}"
            )

        data = resp.json()
        blocks = data.get("blocks", [])
        scores = [b.get("score", 0) for b in blocks] or [0]
        return OCRResult(
            text=data.get("text", ""),
            blocks=blocks,
            latency_ms=data.get("meta", {}).get("latency_ms", 0),
            avg_score=round(sum(scores) / len(scores), 3),
            quality=quality,
        )

    def close(self) -> None:
        self._client.close()
