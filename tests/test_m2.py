"""M2 单测：全局热键解析 + 登录项纯函数。"""

from app import login_item
from app.config import Settings
from app.hotkey import validate_hotkey


def test_hotkey_default_parses():
    assert validate_hotkey(Settings().hotkey)


def test_hotkey_invalid_rejected():
    assert not validate_hotkey("<not_a_key>+$@")


def test_agent_plist_content_roundtrip():
    xml = login_item._agent_plist_content("/usr/bin/python3", "/Users/x/code")
    assert login_item.AGENT_LABEL in xml
    assert "/usr/bin/python3" in xml
    assert "<string>app.main</string>" in xml
    assert "<true/>" in xml   # RunAtLoad


def test_agent_plist_escapes_xml():
    xml = login_item._agent_plist_content("/a&b/python", "/c<d>")
    assert "&amp;" in xml and "&lt;" in xml


def test_bundle_path_detection(monkeypatch):
    monkeypatch.setattr(
        login_item.sys, "executable", "/Apps/snap-ocr.app/Contents/MacOS/snap-ocr"
    )
    assert login_item.app_bundle_path() == "/Apps/snap-ocr.app"
    monkeypatch.setattr(login_item.sys, "executable", "/usr/bin/python3")
    assert login_item.app_bundle_path() is None
