"""更新器单测：纯函数（版本/决策/解析）、MockTransport 网络层、
stage_update 的 ditto 往返（exec 位保留）、安装计划、脚本语法冒烟。"""

import hashlib
import json
import os
import subprocess

import httpx
import pytest

from app import updater
from app.updater import (
    INSTALL_SCRIPT_TEMPLATE,
    Appcast,
    AppcastError,
    UpdateCheckError,
    UpdateClient,
    UpdateDecision,
    can_write_dir,
    cleanup_stale_staging,
    compare_versions,
    decide,
    parse_appcast,
    parse_version,
    plan_installation,
    sha256_file,
    stage_update,
    verify_codesign,
)

_SHA = "a" * 64
_URL = "https://github.com/lonelytears/snap-ocr/releases/download/v1.2.0/snap-ocr-1.2.0-macos.zip"


def _appcast(version="1.2.0", min_required=None, sha=_SHA, url=_URL):
    return Appcast(version=version, min_required=min_required, notes=("n",),
                   url=url, sha256=sha, size=None)


# ── 版本比较 ──────────────────────────────

def test_parse_version():
    assert parse_version("1.2.0") == (1, 2, 0)
    assert parse_version("v1.10.0") == (1, 10, 0)
    assert parse_version("1.2") == (1, 2)
    for bad in ("1.x.0", "abc", "1.2.3.4.5", ""):
        with pytest.raises(ValueError):
            parse_version(bad)


def test_compare_versions():
    assert compare_versions("1.2.0", "1.10.0") < 0   # 数值比较，非字符串
    assert compare_versions("1.10.0", "2.0.0") < 0
    assert compare_versions("1.2", "1.2.0") == 0
    assert compare_versions("v1.2.0", "1.1.9") > 0


# ── appcast 解析 ──────────────────────────────

def test_parse_appcast_ok():
    text = json.dumps({
        "schema_version": 1, "version": "1.2.0", "min_required": "1.0.0",
        "release_notes_zh": ["a", "b"],
        "asset": {"url": _URL, "sha256": _SHA, "size": 100},
    })
    ac = parse_appcast(text)
    assert ac.version == "1.2.0"
    assert ac.min_required == "1.0.0"
    assert ac.notes == ("a", "b")
    assert ac.size == 100


def test_parse_appcast_notes_normalization():
    body = {"version": "1.2.0",
            "asset": {"url": _URL, "sha256": _SHA}}
    assert parse_appcast(json.dumps(body)).notes == ()
    body["release_notes_zh"] = "单条说明"
    assert parse_appcast(json.dumps(body)).notes == ("单条说明",)


@pytest.mark.parametrize("mutate,match", [
    ({"version": "x.y"}, "版本号"),
    ({"min_required": 3}, "min_required"),
    ({"asset": None}, "asset"),
    ({"asset": {"url": "http://x.zip", "sha256": _SHA}}, "https"),
    ({"asset": {"url": "https://x.dmg", "sha256": _SHA}}, "zip"),
    ({"asset": {"url": _URL, "sha256": "xyz"}}, "sha256"),
])
def test_parse_appcast_invalid(mutate, match):
    body = {"version": "1.2.0", "asset": {"url": _URL, "sha256": _SHA}}
    body.update(mutate)
    with pytest.raises(AppcastError, match=match):
        parse_appcast(json.dumps(body))


def test_parse_appcast_bad_json():
    with pytest.raises(AppcastError, match="JSON"):
        parse_appcast("{not json")


# ── 决策矩阵 ──────────────────────────────

def test_decide_matrix():
    assert decide("1.2.0", _appcast(version="1.2.0")) is UpdateDecision.UP_TO_DATE
    assert decide("1.3.0", _appcast(version="1.2.0")) is UpdateDecision.UP_TO_DATE  # 发版方回滚
    assert decide("1.1.0", _appcast(version="1.2.0")) is UpdateDecision.OPTIONAL
    assert decide("1.1.0", _appcast(version="1.2.0", min_required="1.2.0")) is UpdateDecision.FORCED
    # min_required 错配高于 version：只要 current 更低仍强制
    assert decide("1.1.0", _appcast(version="1.2.0", min_required="2.0.0")) is UpdateDecision.FORCED
    ac = _appcast(version="1.2.0", min_required="2.0.0")
    assert decide("1.2.0", ac) is UpdateDecision.UP_TO_DATE


# ── 网络层 ──────────────────────────────

def _update_client(handler) -> UpdateClient:
    return UpdateClient(timeout_s=2.0, transport=httpx.MockTransport(handler))


def test_fetch_appcast_200():
    c = _update_client(lambda req: httpx.Response(200, text='{"version": "1.0"}'))
    assert "1.0" in c.fetch_appcast("https://example.com/appcast.json")


def test_fetch_appcast_errors():
    c = _update_client(lambda req: httpx.Response(404))
    with pytest.raises(UpdateCheckError, match="404"):
        c.fetch_appcast("https://example.com/appcast.json")

    def boom(req):
        raise httpx.ConnectError("no route")

    c = _update_client(boom)
    with pytest.raises(UpdateCheckError, match="网络错误"):
        c.fetch_appcast("https://example.com/appcast.json")


def test_fetch_follows_redirect_chain():
    """GitHub release 资产是 302 链——follow_redirects 漏配整个功能静默失败。"""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/appcast.json":
            return httpx.Response(302, headers={
                "Location": "https://cdn.example.com/real-appcast.json"})
        return httpx.Response(200, text='{"version": "9.9"}')

    c = _update_client(handler)
    assert "9.9" in c.fetch_appcast("https://example.com/appcast.json")


def test_download_progress(tmp_path):
    payload = b"x" * 5000

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload,
                              headers={"Content-Length": str(len(payload))})

    seen = []
    c = _update_client(handler)
    c.download(_URL, tmp_path / "z.zip", progress_cb=seen.append)
    assert (tmp_path / "z.zip").read_bytes() == payload
    assert seen and seen[-1] == 100


# ── verify_codesign ──────────────────────────────

def _fake_codesign(rc_verify=0,
                   dv_output="Authority=snap-ocr-dev\nIdentifier=com.lonelytears.snap-ocr\n"):
    def run(cmd, **kw):
        if "--verify" in cmd:
            return subprocess.CompletedProcess(cmd, rc_verify)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=dv_output.encode())
    return run


def test_verify_codesign_ok(monkeypatch):
    monkeypatch.setattr(updater.subprocess, "run", _fake_codesign())
    assert verify_codesign("/fake/Fake.app") is True


def test_verify_codesign_integrity_fail(monkeypatch):
    monkeypatch.setattr(updater.subprocess, "run", _fake_codesign(rc_verify=1))
    assert verify_codesign("/fake/Fake.app") is False


def test_verify_codesign_wrong_authority(monkeypatch):
    """adhoc 或他人证书签名：无 Authority 行 → 拒绝（保 TCC 授权延续 + 防篡改）。"""
    monkeypatch.setattr(updater.subprocess, "run",
                        _fake_codesign(dv_output="Identifier=x\nTeamIdentifier=not set\n"))
    assert verify_codesign("/fake/Fake.app") is False


# ── stage_update：下载→sha→ditto→验签 ──────────────────────────────

def _make_fake_app(root) -> None:
    """造假 .app（带可执行位的主二进制），供 ditto 打包往返。"""
    exe = root / "Fake.app" / "Contents" / "MacOS" / "snap-ocr"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"#!/bin/bash\nexit 0\n")
    exe.chmod(0o755)


def test_stage_update_roundtrip_keeps_exec_bit(tmp_path, monkeypatch):
    src = tmp_path / "src"
    _make_fake_app(src)
    zipped = tmp_path / "fake.zip"
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent",
                    str(src / "Fake.app"), str(zipped)], check=True)
    sha = sha256_file(zipped)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=zipped.read_bytes())

    monkeypatch.setattr(updater, "verify_codesign", lambda b, a=None: True)
    client = _update_client(handler)
    staged = stage_update(client, _appcast(sha=sha), tmp_path / "staging")
    assert staged.name == "Fake.app"
    exe = staged / "Contents" / "MacOS" / "snap-ocr"
    assert exe.exists()
    assert os.access(exe, os.X_OK), "ditto 解压必须保留可执行位"


def test_stage_update_sha_mismatch(tmp_path, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"tampered")

    client = _update_client(handler)
    with pytest.raises(UpdateCheckError, match="校验失败"):
        stage_update(client, _appcast(sha="b" * 64), tmp_path / "staging")
    assert not (tmp_path / "staging" / "snap-ocr.zip").exists()


def test_stage_update_signature_rejected(tmp_path, monkeypatch):
    src = tmp_path / "src"
    _make_fake_app(src)
    zipped = tmp_path / "fake.zip"
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent",
                    str(src / "Fake.app"), str(zipped)], check=True)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=zipped.read_bytes())

    monkeypatch.setattr(updater, "verify_codesign", lambda b, a=None: False)
    with pytest.raises(UpdateCheckError, match="签名"):
        stage_update(_update_client(handler), _appcast(sha=sha256_file(zipped)),
                     tmp_path / "staging")


def test_stage_update_idempotent_resume(tmp_path, monkeypatch):
    """zip 已在 staging 且 sha 匹配 → 不再发网络请求（断点续装）。"""
    src = tmp_path / "src"
    _make_fake_app(src)
    staging = tmp_path / "staging"
    staging.mkdir()
    zipped = staging / "snap-ocr.zip"
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent",
                    str(src / "Fake.app"), str(zipped)], check=True)

    def handler(req):  # pragma: no cover - 不应被调用
        raise AssertionError("不应重新下载")

    monkeypatch.setattr(updater, "verify_codesign", lambda b, a=None: True)
    staged = stage_update(_update_client(handler), _appcast(sha=sha256_file(zipped)), staging)
    assert staged.name == "Fake.app"


# ── 安装计划 ──────────────────────────────

def _staged(tmp_path):
    app = tmp_path / "staging" / "1.2.0" / "extracted" / "snap-ocr.app"
    app.mkdir(parents=True)
    return app


def test_plan_installation_dev_mode(tmp_path):
    plan = plan_installation(None, _staged(tmp_path), old_pid=1)
    assert plan.mode == "manual"
    assert plan.reveal_path is not None


def test_plan_installation_auto(tmp_path):
    apps = tmp_path / "Applications"
    apps.mkdir()
    staged = _staged(tmp_path)
    plan = plan_installation(str(apps / "snap-ocr.app"), staged, old_pid=42)
    assert plan.mode == "auto"
    assert plan.script_path.exists()
    assert str(plan.script_path).startswith(str(tmp_path))
    assert "42" in plan.argv
    assert str(apps / "snap-ocr.app") in plan.argv


def test_plan_installation_readonly_target(tmp_path):
    apps = tmp_path / "Applications"
    apps.mkdir()
    apps.chmod(0o555)
    try:
        if can_write_dir(apps):  # 以 root 跑时 0555 仍可写，跳过
            pytest.skip("running as root")
        plan = plan_installation(str(apps / "snap-ocr.app"), _staged(tmp_path), old_pid=1)
        assert plan.mode == "manual"
    finally:
        apps.chmod(0o755)


def test_plan_installation_dmg_volume(tmp_path):
    staged = _staged(tmp_path)
    plan = plan_installation("/Volumes/snap-ocr 1.2/snap-ocr.app", staged, old_pid=1)
    assert plan.mode == "manual"


def test_install_script_syntax(tmp_path):
    script = tmp_path / "install_update.sh"
    script.write_text(INSTALL_SCRIPT_TEMPLATE)
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()


# ── 残留清理 ──────────────────────────────

def test_cleanup_stale_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "STAGING_ROOT", tmp_path / "staging")
    (tmp_path / "staging" / "1.2.0").mkdir(parents=True)
    old_backup = tmp_path / "Applications" / "snap-ocr.app.old-123"
    old_backup.mkdir(parents=True)

    cleanup_stale_staging(str(tmp_path / "Applications" / "snap-ocr.app"))
    assert not (tmp_path / "staging" / "1.2.0").exists()
    assert not old_backup.exists()


def test_can_write_dir(tmp_path):
    assert can_write_dir(tmp_path) is True
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        if can_write_dir(ro):
            pytest.skip("running as root")
        assert can_write_dir(ro) is False
    finally:
        ro.chmod(0o755)


def test_sha256_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    assert sha256_file(f) == hashlib.sha256(b"hello").hexdigest()
