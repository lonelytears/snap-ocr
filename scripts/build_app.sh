#!/bin/bash
# 构建 dist/snap-ocr.app（onedir，LSUIElement 无 Dock 图标）并安装到 /Applications
# 用法: scripts/build_app.sh [--no-install]   --no-install 只构建+签名，不装本机（发布用）
set -euo pipefail
cd "$(dirname "$0")/.."

# 版本号唯一来源：app/__init__.py
APP_VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py)
[ -n "$APP_VERSION" ] || { echo "❌ app/__init__.py 缺少 __version__"; exit 1; }

uv run pyinstaller --noconfirm snap-ocr.spec

APP="dist/snap-ocr.app"

# 本地证书签名：身份跨构建稳定 → TCC 授权（辅助功能/输入监控/屏幕录制）
# 不再因重打包失效。证书见 docs（CN=snap-ocr-dev，已导入登录钥匙串）。
if codesign --force --sign snap-ocr-dev "$APP" 2>/dev/null; then
  echo "🔐 已用本地证书签名（重打包后授权保持有效）"
else
  echo "⚠️  本地签名证书不可用，保持 adhoc——每次重打包后需重新授权"
fi
echo ""

if [ "${1:-}" = "--no-install" ]; then
  echo "✅ 构建完成: ${APP}（v${APP_VERSION}，未安装本机）"
  exit 0
fi

# 安装到 /Applications：TCC 授权按"签名身份+路径"记录，固定运行路径后，
# 每次重打包覆盖，辅助功能/输入监控/屏幕录制授权均保持有效。
TARGET="/Applications/snap-ocr.app"
RUNNING=0
if pgrep -x snap-ocr >/dev/null 2>&1; then
  RUNNING=1
  echo "🛑 snap-ocr 正在运行，先退出…"
  pkill -x snap-ocr || true
  sleep 1
fi
[ -d "$TARGET" ] && rm -rf "$TARGET"
if cp -R "$APP" "$TARGET"; then
  if codesign --verify "$TARGET" >/dev/null 2>&1; then
    echo "📦 已安装到 ${TARGET}（签名校验通过）"
  else
    echo "📦 已安装到 ${TARGET}（⚠️ 签名校验未通过，授权可能失效）"
  fi
else
  echo "⚠️  无法写入 /Applications，app 保留在 $APP"
  exit 1
fi

if [ "$RUNNING" = 1 ]; then
  open "$TARGET"
  echo "🔁 已重新启动（${TARGET}）"
fi
echo ""
echo "✅ 构建完成: ${TARGET}（v${APP_VERSION}）"
echo "   ⚠️ 从 dist 路径迁移到 /Applications 后，首次需重新授权（仅此一次）："
echo "   1) 辅助功能（全局热键）：系统设置 → 隐私与安全性 → 辅助功能 → 勾选 snap-ocr"
echo "   2) 屏幕录制（截图会话）：同路径勾选 snap-ocr"
echo "   3) 托盘菜单 → 重新开启一次开机自启（登录项绑定旧 dist 路径，已失效）"
