"""配置管理：三层合并 + 用户面板持久化。

优先级（高→低）：用户配置 JSON（设置面板写入） > 环境变量/.env（SNAP_ 前缀） > 默认值。
用户 JSON 放 ~/Library/Application Support/snap-ocr/config.json——
打包成 .app 后 cwd 是 /，.env 方案失效，这是持久化的正解。
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "snap-ocr"
CONFIG_PATH = APP_SUPPORT_DIR / "config.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SNAP_", extra="ignore")

    # ── OCR 服务 ──
    ocr_base_url: str = "http://127.0.0.1:8000"   # 设置面板可改远程地址（朋友指向你的局域网机）
    ocr_quality: str = "fast"            # fast | accurate
    ocr_timeout_s: float = 3.0

    # ── 快捷键（pynput 语法：<alt>+<shift>+o）──
    hotkey: str = "<alt>+<shift>+o"      # 截图：标注会话，完成=进剪贴板
    hotkey_ocr: str = "<alt>+<shift>+i"  # 截图并识别：完成=直接 OCR

    # ── 大模型（预留：下版本「截图翻译」使用，当前仅存储）──
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # ── 界面 ──
    history_size: int = 20
    result_font_pt: int = 14

    # ── 更新（appcast 托管在 GitHub Releases，latest 直链永久有效）──
    update_check_url: str = (
        "https://github.com/lonelytears/snap-ocr/releases/latest/download/appcast.json"
    )
    update_auto_check: bool = True
    # 截图后端: overlay=自绘选区+标注(需屏幕录制权限) | system=系统交互(免权限无标注)
    capture_backend: str = "overlay"
    # 剪贴板输出比例: logical=与框选视觉1:1(默认, 无缩放歧义) | device=Retina 2x物理(更清晰)
    clipboard_scale: str = "logical"


def _read_user_json() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _user_kwargs() -> dict:
    """只取模型已有字段，忽略历史残留键。"""
    fields = Settings.model_fields
    return {k: v for k, v in _read_user_json().items() if k in fields}


@lru_cache
def get_settings() -> Settings:
    return Settings(**_user_kwargs())


def update_user_config(updates: dict) -> Settings:
    """设置面板保存入口：合并落盘 → 清缓存 → 返回新 Settings。"""
    data = _read_user_json()
    data.update(updates)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    get_settings.cache_clear()
    return get_settings()


def reset_user_config() -> Settings:
    """恢复默认：删除用户 JSON。"""
    CONFIG_PATH.unlink(missing_ok=True)
    get_settings.cache_clear()
    return get_settings()
