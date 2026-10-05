"""检查更新 + 强制更新。

分层（纯函数在顶部，测试免 GUI）：
  纯函数：版本比较 / appcast 解析 / 更新决策 / sha256 / 签名校验
  网络：UpdateClient（httpx，follow_redirects 必开——GitHub 直链是 302 链）
  worker：QThread 检查/下载（沿用 tray._RecognizeWorker 模式）
  安装：plan_installation 决定 auto 原位替换 / manual 引导手动；detached 脚本执行交换

策略：fail-open——检查/下载任一失败都不阻断使用，只有 appcast 声明
min_required 且当前版本低于它时才强制（弹窗不可跳过 + 托盘主功能 gate）。
"""

from __future__ import annotations

import enum
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
from PySide6.QtCore import QObject, QThread, Signal

from app import __version__

STAGING_ROOT = Path.home() / "Library" / "Application Support" / "snap-ocr" / "update-staging"

# 与 build_app.sh 的本地证书 CN 一致；更新包必须同证书签名，用户机器上的
# TCC 授权（输入监控/辅助功能/屏幕录制）才跨版本延续。
EXPECTED_SIGN_AUTHORITY = "snap-ocr-dev"


# ── 纯函数层 ──────────────────────────────────────


class UpdateCheckError(Exception):
    """检查/下载过程中的可预期失败（网络、格式、校验）——fail-open 放行。"""


class AppcastError(UpdateCheckError):
    """appcast 内容非法。"""


class UpdateDecision(enum.Enum):
    UP_TO_DATE = "up_to_date"
    OPTIONAL = "optional"
    FORCED = "forced"


@dataclass(frozen=True)
class Appcast:
    version: str
    min_required: str | None
    notes: tuple[str, ...]
    url: str
    sha256: str
    size: int | None


def parse_version(s: str) -> tuple[int, ...]:
    """'v1.10.0' → (1, 10, 0)。段数 1-4、纯数字，否则 ValueError。"""
    s = str(s).strip().lstrip("vV")
    parts = s.split(".")
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"版本号段数非法: {s!r}")
    try:
        return tuple(int(p) for p in parts)
    except ValueError as e:
        raise ValueError(f"版本号含非数字段: {s!r}") from e


def compare_versions(a: str, b: str) -> int:
    """补零对齐比较：1.2.0 < 1.10.0 < 2.0.0，1.2 == 1.2.0。"""
    ta, tb = parse_version(a), parse_version(b)
    width = max(len(ta), len(tb))
    ta += (0,) * (width - len(ta))
    tb += (0,) * (width - len(tb))
    return (ta > tb) - (ta < tb)


def parse_appcast(text: str) -> Appcast:
    """解析并校验 appcast JSON，非法抛 AppcastError。"""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise AppcastError(f"appcast 不是合法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise AppcastError("appcast 顶层必须是对象")

    version = data.get("version")
    if not isinstance(version, str):
        raise AppcastError("缺少 version 字段")
    try:
        parse_version(version)
    except ValueError as e:
        raise AppcastError(str(e)) from e

    min_required = data.get("min_required")
    if min_required is not None:
        if not isinstance(min_required, str):
            raise AppcastError("min_required 必须是版本号字符串或 null")
        try:
            parse_version(min_required)
        except ValueError as e:
            raise AppcastError(f"min_required 非法: {e}") from e

    asset = data.get("asset")
    if not isinstance(asset, dict):
        raise AppcastError("缺少 asset 对象")
    url = asset.get("url")
    if not isinstance(url, str) or not url.startswith("https://") or not url.endswith(".zip"):
        raise AppcastError("asset.url 必须是 https:// 开头、.zip 结尾的下载地址")
    sha = asset.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise AppcastError("asset.sha256 必须是 64 位十六进制")

    size = asset.get("size")
    if size is not None and not isinstance(size, int):
        raise AppcastError("asset.size 必须是整数或省略")

    raw_notes = data.get("release_notes_zh", [])
    if isinstance(raw_notes, str):
        notes: tuple[str, ...] = (raw_notes,)
    elif isinstance(raw_notes, list) and all(isinstance(n, str) for n in raw_notes):
        notes = tuple(raw_notes)
    elif raw_notes in (None, []):
        notes = ()
    else:
        raise AppcastError("release_notes_zh 必须是字符串或字符串列表")

    return Appcast(version, min_required, notes, url, sha, size)


def decide(current: str, appcast: Appcast) -> UpdateDecision:
    """当前版本不低于 appcast 版本 → 最新；低于 min_required → 强制；否则可选。

    min_required 高于 appcast.version 的错配：只要 current 两边都低仍判
    FORCED——发版方的错配不应放行旧版本。
    """
    if compare_versions(current, appcast.version) >= 0:
        return UpdateDecision.UP_TO_DATE
    if appcast.min_required and compare_versions(current, appcast.min_required) < 0:
        return UpdateDecision.FORCED
    return UpdateDecision.OPTIONAL


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_codesign(bundle: Path, authority: str = EXPECTED_SIGN_AUTHORITY) -> bool:
    """完整性 + 签名身份双验：codesign --verify 通过，且 Authority 行含指定身份。

    自签证书的 codesign -dv 默认不输出 Authority，必须 --verbose=4（实测）。
    """
    try:
        v = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
            capture_output=True, timeout=30,
        )
        if v.returncode != 0:
            return False
        d = subprocess.run(
            ["/usr/bin/codesign", "-dv", "--verbose=4", str(bundle)],
            capture_output=True, timeout=30,
        )
        out = (d.stdout + d.stderr).decode("utf-8", "replace")
        return any(line.strip() == f"Authority={authority}" for line in out.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        return False


def can_write_dir(d: Path) -> bool:
    """真实试探写权限（os.access 在 macOS ACL 下不准）。"""
    try:
        probe = d / ".snap-ocr-write-probe"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


# ── 网络层 ──────────────────────────────────────


class UpdateClient:
    """appcast 拉取 + 流式下载。follow_redirects 必开：GitHub release 资产是 302 链。"""

    def __init__(self, timeout_s: float = 10.0, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(timeout=timeout_s, follow_redirects=True, transport=transport)

    def fetch_appcast(self, url: str) -> str:
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as e:
            raise UpdateCheckError(f"网络错误: {e}") from e
        if resp.status_code != 200:
            raise UpdateCheckError(f"HTTP {resp.status_code}")
        return resp.text

    def download(self, url: str, dest: Path, progress_cb=None) -> None:
        """流式下载到 dest；progress_cb(0-100)，未知总大小时不回调百分比。"""
        try:
            with self._client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise UpdateCheckError(f"下载失败: HTTP {resp.status_code}")
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb and total:
                            progress_cb(min(99, int(done * 100 / total)))
        except httpx.HTTPError as e:
            raise UpdateCheckError(f"下载中断: {e}") from e
        if progress_cb:
            progress_cb(100)

    def close(self) -> None:
        self._client.close()


# ── 下载落盘（纯逻辑，可直测） ──────────────────────────────────────


def stage_update(client: UpdateClient, appcast: Appcast, staging: Path,
                 progress_cb=None) -> Path:
    """下载→sha256→ditto 解压→验签，返回 staged .app 路径。

    必须用 ditto 而非 python zipfile：要保留主可执行文件的 x 权限。
    幂等续装：staging 里已有同版本 zip 且 sha 通过则跳过下载。
    """
    staging.mkdir(parents=True, exist_ok=True)
    zipped = staging / "snap-ocr.zip"

    if not (zipped.exists() and sha256_file(zipped) == appcast.sha256):
        if progress_cb:
            progress_cb(0)
        client.download(appcast.url, zipped, progress_cb=progress_cb)
        if sha256_file(zipped) != appcast.sha256:
            zipped.unlink(missing_ok=True)
            raise UpdateCheckError("安装包校验失败（可能被篡改或下载中断）")
    elif progress_cb:
        progress_cb(100)

    extracted = staging / "extracted"
    if extracted.exists():
        shutil.rmtree(extracted)
    r = subprocess.run(
        ["/usr/bin/ditto", "-x", "-k", str(zipped), str(extracted)],
        capture_output=True, timeout=300,
    )
    if r.returncode != 0:
        raise UpdateCheckError("安装包解压失败")
    apps = sorted(extracted.glob("*.app"))
    if not apps:
        raise UpdateCheckError("安装包里没有 .app")

    staged = apps[0]
    if not verify_codesign(staged):
        raise UpdateCheckError("安装包签名身份异常（非本家证书），已拒绝安装")
    return staged


# ── 安装编排 ──────────────────────────────────────

INSTALL_SCRIPT_TEMPLATE = """#!/bin/bash
# snap-ocr 自更新安装：等待旧进程退出 → 原子交换 → 拉起新版 → 清理
# usage: install_update.sh <old_pid> <target_bundle> <staged_app> <log_path>
set -u

OLD_PID="$1"; TARGET="$2"; STAGED="$3"; LOG="$4"
log() { printf '%s %s\\n' "$(date '+%F %T')" "$*" >>"$LOG" 2>/dev/null; }

# 1. 等旧进程退出（上限 20s；超时强杀——onedir 懒加载 .so，不退就删有崩溃风险）
i=0
while kill -0 "$OLD_PID" 2>/dev/null && [ "$i" -lt 100 ]; do sleep 0.2; i=$((i+1)); done
if kill -0 "$OLD_PID" 2>/dev/null; then kill -9 "$OLD_PID" 2>/dev/null; sleep 0.5; fi

# 2. 复验签名（纵深防御：下载校验后、安装前防替换）
/usr/bin/codesign --verify --deep --strict "$STAGED" 2>>"$LOG" \\
  || { log "ERROR staged app 签名校验失败"; exit 1; }

# 3. 同卷原子交换：旧→备份，新→目标；新失败则回滚
BACKUP="${TARGET}.old-$$"
if ! mv "$TARGET" "$BACKUP"; then log "ERROR 无法移走旧版（无写权限？）"; exit 2; fi
if mv "$STAGED" "$TARGET"; then
  xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null
  log "OK 已安装 $TARGET"
  open "$TARGET"
  sleep 5
  if pgrep -x snap-ocr >/dev/null 2>&1; then
    rm -rf "$BACKUP"
    log "OK 新版运行正常，已清备份"
  else
    log "WARN 未检测到新版进程，保留备份 $BACKUP"
  fi
  rm -rf "$(dirname "$STAGED")"
else
  mv "$BACKUP" "$TARGET"
  log "ERROR 新版移入失败，已回滚"
  exit 3
fi
"""


@dataclass(frozen=True)
class InstallPlan:
    mode: str  # "auto" 原位替换 | "manual" 引导用户手动
    script_path: Path | None = None
    argv: tuple = ()
    reveal_path: Path | None = None


def plan_installation(running_bundle: str | None, staged_app: Path,
                      old_pid: int) -> InstallPlan:
    """决定安装方式：写不了目标位置就 manual，否则生成 detached 脚本计划。"""
    if running_bundle is None:  # 开发态（uv run），无 bundle 可替换
        return InstallPlan("manual", reveal_path=staged_app)
    target = Path(running_bundle)
    if str(target).startswith("/Volumes/"):  # DMG/只读卷里直接跑
        return InstallPlan("manual", reveal_path=staged_app)
    if not can_write_dir(target.parent):
        return InstallPlan("manual", reveal_path=staged_app)

    staging = staged_app.parent.parent  # staging/<version>/extracted/xx.app → staging/<version>
    script = staging / "install_update.sh"
    script.write_text(INSTALL_SCRIPT_TEMPLATE)
    script.chmod(0o755)
    argv = ("/bin/bash", str(script), str(old_pid), str(target), str(staged_app),
            str(STAGING_ROOT / "install.log"))
    return InstallPlan("auto", script_path=script, argv=argv)


def launch_detached(argv, log_path: Path) -> None:
    """新会话启动安装脚本：父进程（本应用）随即退出，脚本继续完成交换。"""
    with open(log_path, "ab") as log:
        subprocess.Popen(  # noqa: S603 - argv 为固定元组，无 shell
            list(argv), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True,
        )


def cleanup_stale_staging(running_bundle: str | None) -> None:
    """启动时清理上次更新残留：staging 目录 + bundle 旁的 .old-* 备份。"""
    if running_bundle:
        target = Path(running_bundle)
        for old in target.parent.glob(target.name + ".old-*"):
            shutil.rmtree(old, ignore_errors=True)
    if STAGING_ROOT.exists():
        for child in STAGING_ROOT.iterdir():
            shutil.rmtree(child, ignore_errors=True)


# ── QThread worker（沿用 tray._RecognizeWorker 模式） ──────────────────


class UpdateCheckWorker(QThread):
    finished_ok = Signal(object)  # Appcast
    failed = Signal(str)

    def __init__(self, client: UpdateClient, url: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._url = url

    def run(self) -> None:
        try:
            appcast = parse_appcast(self._client.fetch_appcast(self._url))
            self.finished_ok.emit(appcast)
        except UpdateCheckError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 — worker 兜底，任何异常都要桥回 UI
            self.failed.emit(f"检查更新出错: {e}")


class UpdateDownloadWorker(QThread):
    progress = Signal(int)  # 0-100
    finished_ok = Signal(object)  # staged .app 的 Path（已通过全部校验）
    failed = Signal(str)

    def __init__(self, client: UpdateClient, appcast: Appcast, staging: Path, parent=None):
        super().__init__(parent)
        self._client = client
        self._appcast = appcast
        self._staging = staging

    def run(self) -> None:
        try:
            staged = stage_update(self._client, self._appcast, self._staging,
                                  progress_cb=self.progress.emit)
            self.finished_ok.emit(staged)
        except UpdateCheckError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"安装准备失败: {e}")


# ── 编排器（托盘 ↔ worker 之间的桥） ──────────────────────────────────


class UpdateController(QObject):
    check_done = Signal(object, object)   # UpdateDecision, Appcast
    check_failed = Signal(str)            # 手动触发才发；自动触发静默（fail-open）
    manual_uptodate = Signal(str)         # 手动检查且已是最新（版本号）
    download_progress = Signal(int)
    download_done = Signal(object)        # InstallPlan
    download_failed = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._client = UpdateClient()
        self._check_worker: UpdateCheckWorker | None = None
        self._dl_worker: UpdateDownloadWorker | None = None
        self._manual = False

    def check(self, manual: bool = False) -> None:
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        self._manual = manual
        w = UpdateCheckWorker(self._client, self._settings.update_check_url)
        w.finished_ok.connect(self._on_appcast)
        w.failed.connect(self._on_check_failed)
        self._check_worker = w
        w.start()

    def _on_appcast(self, appcast: Appcast) -> None:
        decision = decide(__version__, appcast)
        self.check_done.emit(decision, appcast)
        if self._manual and decision is UpdateDecision.UP_TO_DATE:
            self.manual_uptodate.emit(appcast.version)

    def _on_check_failed(self, msg: str) -> None:
        if self._manual:
            self.check_failed.emit(msg)

    def download_and_stage(self, appcast: Appcast) -> None:
        if self._dl_worker is not None and self._dl_worker.isRunning():
            return
        w = UpdateDownloadWorker(self._client, appcast, STAGING_ROOT / appcast.version)
        w.progress.connect(self.download_progress)
        w.finished_ok.connect(self._on_staged)
        w.failed.connect(self.download_failed)
        self._dl_worker = w
        w.start()

    def _on_staged(self, staged_app: Path) -> None:
        from app.login_item import app_bundle_path

        self.download_done.emit(
            plan_installation(app_bundle_path(), staged_app, os.getpid()))

    def execute(self, plan: InstallPlan) -> None:
        if plan.mode == "auto":
            launch_detached(plan.argv, STAGING_ROOT / "install.log")
        else:
            subprocess.Popen(["/usr/bin/open", "-R", str(plan.reveal_path)])  # noqa: S603,S607

    def apply_settings(self, settings) -> None:
        self._settings = settings
