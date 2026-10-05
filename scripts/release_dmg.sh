#!/bin/bash
# 发布 DMG：staging（.app + Applications 快捷方式 + 使用说明）→ UDZO 压缩
# 用法: scripts/release_dmg.sh [版本号]   版本号缺省读 app/__init__.py 的 __version__
set -euo pipefail
cd "$(dirname "$0")/.."

VER="${1:-v$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py)}"
[ -n "$VER" ] || { echo "❌ 无法确定版本号"; exit 1; }
APP="dist/snap-ocr.app"
DMG="dist/snap-ocr-${VER}.dmg"
STAGE="dist/.dmg-stage"

[ -d "$APP" ] || { echo "❌ 先运行 scripts/build_app.sh"; exit 1; }

rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
if [ -f "dist/使用说明.txt" ]; then
  cp "dist/使用说明.txt" "$STAGE/"
fi

rm -f "$DMG"
hdiutil create -volname "snap-ocr ${VER}" \
  -srcfolder "$STAGE" -format UDZO -ov "$DMG" >/dev/null

rm -rf "$STAGE"
echo "✅ 发布包: $DMG ($(du -h "$DMG" | cut -f1))"
