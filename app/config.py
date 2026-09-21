"""配置管理：与 ocr-service 同风格，环境变量/SNAP_ 前缀覆盖。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SNAP_", extra="ignore")

    # ── OCR 服务 ──
    ocr_base_url: str = "http://127.0.0.1:8000"
    ocr_quality: str = "fast"            # fast | accurate
    ocr_timeout_s: float = 3.0

    # ── 快捷键(M2 启用) ──
    hotkey: str = "<alt>+<shift>+o"

    # ── 界面 ──
    history_size: int = 20
    result_font_pt: int = 14
    # 截图后端: overlay=自绘选区+标注(需屏幕录制权限) | system=系统交互(免权限无标注)
    capture_backend: str = "overlay"
    # 剪贴板输出比例: logical=与框选视觉1:1(默认, 无缩放歧义) | device=Retina 2x物理(更清晰)
    clipboard_scale: str = "logical"


@lru_cache
def get_settings() -> Settings:
    return Settings()
