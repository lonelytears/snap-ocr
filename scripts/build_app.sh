#!/bin/bash
# 构建 dist/snap-ocr.app（onedir，LSUIElement 无 Dock 图标）
set -euo pipefail
cd "$(dirname "$0")/.."

uv run pyinstaller --noconfirm snap-ocr.spec

APP="dist/snap-ocr.app"
echo ""
echo "✅ 构建完成: $APP"
echo "   首次运行需授权（各一次）："
echo "   1) open $APP"
echo "   2) 辅助功能（全局热键）：系统设置 → 隐私与安全性 → 辅助功能 → 加号添加 $APP"
echo "   3) 屏幕录制（截图会话）：同路径勾选 snap-ocr"
echo "   4) 托盘菜单 → 开机自启（SMAppService 原生登录项）"
