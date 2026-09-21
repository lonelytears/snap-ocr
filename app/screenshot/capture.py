"""ScreenCapture 与 DisplayManager（V2 §4.2/§5.2）。

多屏根治方案：Qt 的 grabWindow 在多屏下永远只抓主屏（V1 的根因性错误），
改用系统 `screencapture -R<x,y,w,h>` 按全局矩形定向抓取鼠标所在显示器。
屏幕只抓一次（§21），之后全部基于 Frozen Screenshot 渲染。
"""

import subprocess
import tempfile

from PySide6.QtCore import QRect
from PySide6.QtGui import QCursor, QGuiApplication, QPixmap


class DisplayManager:
    """第一版：以鼠标所在显示器为会话目标显示器（§5.2）。

    display_id(): NSScreen 的 deviceDescription 拿 CGDirectDisplayID，
    用于 screencapture -D 定向整屏抓取（-R 全局矩形在混合缩放多屏下不可靠）。
    注意 NSScreen 坐标为 Cocoa 系（主屏左下原点），需翻转到 Qt 的左上原点。
    """

    @staticmethod
    def target_screen():
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    @staticmethod
    def display_id(screen) -> int | None:
        try:
            from AppKit import NSScreen

            screens = NSScreen.screens()
            main_frame = screens[0].frame() if screens else None
            for ns in screens:
                f = ns.frame()
                qt_y = None
                if main_frame is not None:
                    qt_y = main_frame.size.height - f.origin.y - f.size.height
                same_size = abs(f.size.width - screen.geometry().width()) <= 1 \
                    and abs(f.size.height - screen.geometry().height()) <= 1
                if not same_size:
                    continue
                # 主屏原点必为 (0,0)；副屏用翻转后的 y 与 x 匹配
                if abs(f.origin.x - screen.geometry().x()) <= 1 and (
                    qt_y is None or abs(qt_y - screen.geometry().y()) <= 1
                ):
                    return int(ns.deviceDescription().get("NSScreenNumber", 0)) or None
        except Exception:  # noqa: BLE001 — 无 pyobjc 时走 -R 回退
            pass
        return None


class ScreenshotSnapshot:
    """§4.2：Capture 的产物，Overlay 创建之前完成。"""

    def __init__(self, image: QPixmap, screen_geometry: QRect, scale_factor: float):
        self.image = image            # 冻结图（devicePixelRatio 已设，逻辑尺寸=屏幕）
        self.screen_bounds = screen_geometry
        self.scale_factor = scale_factor

    @property
    def logical_size(self) -> tuple[int, int]:
        return self.image.width(), self.image.height()


def build_capture_command(rect: QRect, output: str) -> list[str]:
    """纯函数便于单测：按全局矩形构造 screencapture 命令。"""
    return [
        "screencapture", "-x",
        "-R", f"{rect.x()},{rect.y()},{rect.width()},{rect.height()}",
        "-t", "png", output,
    ]


class ScreenCapture:
    """捕获指定屏：先于 Overlay 完成（Screenshot First 原则）。

    尺寸归一化策略（根治 dpr 语义漂移）：任何来源的图先转 QImage
    （size() 恒为纯物理像素，无 DPI 解释歧义），强制缩放到
    「屏幕逻辑尺寸 × 屏幕 dpr」，再打上 dpr——出口尺寸永远精确。
    """

    def _normalize(self, image, screen) -> QPixmap | None:
        from PySide6.QtCore import QSize, Qt
        from PySide6.QtGui import QImage

        geo = screen.geometry()
        dpr = screen.devicePixelRatio()
        img = image if isinstance(image, QImage) else image.toImage()
        if img.isNull():
            return None
        target = QSize(int(geo.width() * dpr), int(geo.height() * dpr))
        if img.size() != target:
            img = img.scaled(
                target,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        return pm

    def _capture_by_display_index(self, screen, path: str) -> bool:
        """按显示器序号整屏抓取（内容锚定显示器，不受全局布局影响的正解）。

        screencapture -D 接受 1..N 序号（非 CGDirectDisplayID）；
        序号按 NSScreen 枚举顺序试，并用「像素尺寸 == 屏幕逻辑×dpr」自校验。
        """
        from PySide6.QtGui import QImage

        try:
            from AppKit import NSScreen

            ns_screens = list(NSScreen.screens())
        except Exception:  # noqa: BLE001
            return False
        geo = screen.geometry()
        dpr = screen.devicePixelRatio()
        target_px = (int(geo.width() * dpr), int(geo.height() * dpr))
        for idx, ns in enumerate(ns_screens, start=1):
            f = ns.frame()
            if abs(f.size.width - geo.width()) > 1 or abs(f.size.height - geo.height()) > 1:
                continue
            try:
                subprocess.run(
                    ["screencapture", "-x", "-D", str(idx), "-t", "png", path],
                    check=True, timeout=10,
                )
                img = QImage(path)
                if (img.width(), img.height()) == target_px:
                    return True
            except Exception:  # noqa: BLE001 — 该序号失败换下一个
                continue
        return False

    def capture(self, screen=None) -> ScreenshotSnapshot:
        # 权限前置自检：被拒时明确报错，绝不静默返回"只有壁纸"的图
        from app.screenshot.session import has_screen_permission

        if not has_screen_permission():
            raise RuntimeError(
                "缺少「屏幕录制」权限：系统设置 → 隐私与安全性 → 屏幕录制 → "
                "勾选启动本程序的终端/App 后重启 snap-ocr"
            )
        screen = screen or DisplayManager.target_screen()
        geo = screen.geometry()
        path = tempfile.gettempdir() + "/snap_session_freeze.png"
        dpr = screen.devicePixelRatio()
        import os

        if os.path.exists(path):
            os.remove(path)  # 清掉上会话残留，保证诊断文件新鲜

        image = None
        from PySide6.QtGui import QImage

        source = ""
        try:
            ok = self._capture_by_display_index(screen, path)
            source = "display-index" if ok else "rect-R"
            if not ok:
                subprocess.run(build_capture_command(geo, path), check=True, timeout=10)
            raw = QImage(path)
            print(
                f"[capture] source={source} raw={raw.width()}x{raw.height()} "
                f"target={int(geo.width() * dpr)}x{int(geo.height() * dpr)} "
                f"screen={screen.name()} geo=({geo.x()},{geo.y()},{geo.width()},{geo.height()})",
                flush=True,
            )
            image = self._normalize(raw, screen)
        except Exception as e:  # noqa: BLE001 — 命令抓取失败回退 Qt 原生
            print(f"[capture] cmd-failed({e}) fallback=grabWindow", flush=True)
        if image is None:
            image = self._normalize(screen.grabWindow(0), screen)
            gw = f"{image.width()}x{image.height()}"
            print(f"[capture] source=grabWindow normalized={gw}", flush=True)
        if image is None or image.isNull():
            raise RuntimeError("屏幕捕获失败")
        return ScreenshotSnapshot(image, geo, dpr)
