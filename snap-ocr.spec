# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec：onedir .app（托盘常驻，启动速度与体积优于 onefile）
# 构建：scripts/build_app.sh 或 uv run pyinstaller --noconfirm snap-ocr.spec

a = Analysis(
    ["app/main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="snap-ocr",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 托盘应用：不弹终端
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="snap-ocr",
)

app = BUNDLE(
    coll,
    name="snap-ocr.app",
    icon=None,               # 托盘图标程序化绘制，无需 .icns
    info_plist={
        "LSUIElement": True,           # Agent 应用：无 Dock 图标无主菜单
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleIdentifier": "com.lonelytears.snap-ocr",
        "NSHumanReadableCopyright": "local use",
    },
)
