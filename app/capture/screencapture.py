"""截图采集层：后端接口 + 统一产物契约 + SystemBackend 实现。

关键设计：CaptureBackend 接口隔离具体实现（System=系统交互 / Overlay=自绘标注），
上层(tray/hotkey) 只依赖接口与 CaptureOutcome。
"""

import os
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


class CaptureCancelled(Exception):
    """用户取消截图——正常流程，静默结束。"""


@dataclass
class CaptureOutcome:
    """一次截图会话的产物。"""

    image_path: str              # 最终图（可能含标注渲染）
    wants_ocr: bool              # True=调识别；False=仅产出/保存
    saved_to: str | None = None  # 若用户点了保存，落盘路径


class CaptureBackend(ABC):
    @abstractmethod
    def capture(self) -> CaptureOutcome | None:
        """交互式截取；用户取消返回 None。"""


def build_command(output_path: str, *, interactive: bool = True, window_mode: bool = False
                  ) -> list[str]:
    """纯函数便于单测：拼装 screencapture 参数。

    -i         交互式(十字线拖框, 空格切窗口选择)
    -x         静音(不播快门声)
    """
    cmd = ["screencapture"]
    if interactive:
        cmd.append("-i")
        if window_mode:
            cmd.append("-W")
    cmd += ["-x", output_path]
    return cmd


class SystemBackend(CaptureBackend):
    """系统原生交互截图：免屏幕录制权限（用户手势即授权）。"""

    def capture(self, *, window_mode: bool = False) -> CaptureOutcome:
        path = os.path.join(tempfile.gettempdir(), f"snap_{int(time.time() * 1000)}.png")
        cmd = build_command(path, interactive=True, window_mode=window_mode)
        try:
            # 交互时长由用户决定，给足上限；超时视为异常
            subprocess.run(cmd, check=True, timeout=120)
        except subprocess.TimeoutExpired as e:
            raise TimeoutError("截图交互超时(120s)") from e
        except subprocess.CalledProcessError as e:
            # Esc 取消时 screencapture 非零退出
            if not os.path.exists(path):
                raise CaptureCancelled() from e
            raise
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise CaptureCancelled()
        return CaptureOutcome(image_path=path, wants_ocr=True)
