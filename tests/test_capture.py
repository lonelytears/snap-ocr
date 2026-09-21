"""截图命令拼装测试（纯逻辑，不真截）。"""

from app.capture.screencapture import build_command


def test_interactive_command():
    cmd = build_command("/tmp/snap_1.png")
    assert cmd == ["screencapture", "-i", "-x", "/tmp/snap_1.png"]


def test_window_mode_adds_flag():
    cmd = build_command("/tmp/snap_2.png", window_mode=True)
    assert "-W" in cmd
    assert cmd.index("-i") < cmd.index("-W")


def test_non_interactive_has_no_i():
    cmd = build_command("/tmp/snap_3.png", interactive=False)
    assert "-i" not in cmd
