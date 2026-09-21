"""开机自启（M2）：双轨实现。

- .app 运行态：SMAppService.mainApp（原生登录项，系统设置里可见可管，免弹窗）
- 开发/脚本态：回退 LaunchAgent plist（~/Library/LaunchAgents/…，
  macOS 13+ 同样出现在「登录项」设置的允许列表里）

判定依据：sys.executable 路径里是否存在 .app 祖先目录（PyInstaller onedir
布局为 snap-ocr.app/Contents/MacOS/snap-ocr）。
"""

import sys
from pathlib import Path
from xml.sax.saxutils import escape

AGENT_LABEL = "com.lonelytears.snap-ocr"


def app_bundle_path() -> str | None:
    """当前进程可执行文件若位于 .app bundle 内则返回其路径。"""
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return str(parent)
    return None


def agent_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def _agent_plist_content(python: str, cwd: str) -> str:
    """纯函数便于单测：LaunchAgent plist（RunAtLoad，无 KeepAlive——崩了不拉起）。"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{escape(python)}</string>
        <string>-m</string>
        <string>app.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{escape(cwd)}</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""


def is_enabled() -> bool:
    bundle = app_bundle_path()
    if bundle is not None:
        from ServiceManagement import SMAppService

        return SMAppService.mainApp().status() == SMAppService.Status.enabled
    return agent_plist_path().exists()


def set_enabled(enabled: bool) -> None:
    """开启/关闭开机自启。.app 态用 SMAppService，否则落 LaunchAgent。"""
    bundle = app_bundle_path()
    if bundle is not None:
        from ServiceManagement import SMAppService

        svc = SMAppService.mainApp()
        if enabled:
            svc.register()
        else:
            svc.unregister()
        return
    plist = agent_plist_path()
    if enabled:
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(_agent_plist_content(sys.executable, str(Path.cwd())))
    else:
        plist.unlink(missing_ok=True)
