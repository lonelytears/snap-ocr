"""更新弹窗测试：双模式按钮集合、强制模式关闭拦截、状态切换。

不 show() 窗口（项目惯例：只做逻辑驱动）；关闭拦截用 result() 验证——
未处理的 QDialog result() 为 0，被 reject 后为 DialogCode.Rejected。
"""

import pytest
from PySide6.QtWidgets import QApplication

from app.ui.update_dialog import UpdateDialog
from app.updater import Appcast

_SHA = "a" * 64
_URL = "https://github.com/lonelytears/snap-ocr/releases/download/v1.2.0/snap-ocr-1.2.0-macos.zip"


@pytest.fixture(scope="module")
def qapp_module():
    return QApplication.instance() or QApplication([])


def _appcast():
    return Appcast(version="1.2.0", min_required=None, notes=("修复热键", "新增更新"),
                   url=_URL, sha256=_SHA, size=None)


def test_optional_mode_buttons(qapp_module):
    dlg = UpdateDialog(_appcast(), forced=False)
    assert dlg.later_btn.text() == "稍后"
    dlg.show()
    dlg.reject()
    assert not dlg.isVisible()          # 非强制：reject 正常关闭


def test_forced_mode_blocks_close(qapp_module):
    dlg = UpdateDialog(_appcast(), forced=True)
    assert dlg.later_btn.text() == "退出应用"
    dlg.show()
    dlg.reject()                       # Esc/代码路径
    assert dlg.isVisible()             # 强制：仍可见，未关闭

    class _Event:
        def __init__(self):
            self.ignored = False

        def ignore(self):
            self.ignored = True

    ev = _Event()
    dlg.closeEvent(ev)                  # 标题栏关闭路径
    assert ev.ignored
    dlg.hide()                          # 清理，不影响后续断言


def test_progress_then_failed_then_retry(qapp_module):
    dlg = UpdateDialog(_appcast(), forced=False)
    dlg.set_progress(50)
    assert not dlg.update_btn.isEnabled()
    dlg.set_failed("下载中断")
    assert dlg.update_btn.isEnabled()
    assert dlg.update_btn.text() == "重试更新"
    dlg.set_installing()
    assert not dlg.update_btn.isEnabled()
    assert not dlg.later_btn.isEnabled()


def test_update_requested_signal(qapp_module):
    dlg = UpdateDialog(_appcast(), forced=True)
    got = []
    dlg.update_requested.connect(lambda: got.append(1))
    dlg.update_btn.click()
    assert got == [1]
