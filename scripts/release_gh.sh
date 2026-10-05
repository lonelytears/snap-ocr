#!/bin/bash
# 发布 GitHub Release：zip 更新包 + appcast.json（客户端自动更新源）
# 用法: scripts/release_gh.sh [tag] [--min-required <ver>] [--draft]
#   tag 缺省读 app/__init__.py 的 __version__（自动加 v 前缀）
#   --min-required 低于该版本的客户端将被强制更新（省略 = 全部可选更新）
#   --draft 仅创建草稿（asset 直链未公开，客户端不可见，用于演练）
set -euo pipefail
cd "$(dirname "$0")/.."

REPO="lonelytears/snap-ocr"

MIN_REQUIRED=""
DRAFT=""
TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --min-required) MIN_REQUIRED="${2:?}"; shift 2 ;;
    --draft) DRAFT="--draft"; shift ;;
    *) TAG="$1"; shift ;;
  esac
done

# 0. 版本断言：app/__init__.py 与 pyproject.toml 必须一致
V=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py)
PV=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
[ "$V" = "$PV" ] || { echo "❌ 版本不一致：app/__init__.py=$V pyproject.toml=$PV"; exit 1; }
TAG="${TAG:-v$V}"

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "❌ tag $TAG 已存在"; exit 1
fi
echo "📦 发布 $TAG (版本 $V)"

# 1. 构建（不装本机）
scripts/build_app.sh --no-install

# 2. zip + sha256（ditto 保留可执行权限）
ZIP="dist/snap-ocr-$V-macos.zip"
rm -f "$ZIP"
(cd dist && ditto -c -k --keepParent snap-ocr.app "snap-ocr-$V-macos.zip")
SHA=$(shasum -a 256 "$ZIP" | cut -d' ' -f1)
SIZE=$(stat -f%z "$ZIP")
echo "🔐 sha256=$SHA ($(du -h "$ZIP" | cut -f1))"

# 3. appcast.json（notes 取 RELEASE 文档里 '- ' 开头的行）
NOTES_FILE="docs/RELEASE-$TAG.md"
[ -f "$NOTES_FILE" ] || { echo "❌ 缺少 ${NOTES_FILE}（发版说明）"; exit 1; }
python3 - "$NOTES_FILE" "$V" "$TAG" "$REPO" "$SHA" "$SIZE" "$MIN_REQUIRED" <<'PY'
import json, sys

notes_file, ver, tag, repo, sha, size, min_req = sys.argv[1:]
notes = [line[2:].strip().strip("*") for line in open(notes_file, encoding="utf-8")
         if line.lstrip().startswith("- ")]
appcast = {
    "schema_version": 1,
    "version": ver,
    "min_required": min_req or None,
    "release_notes_zh": notes,
    "asset": {
        "url": f"https://github.com/{repo}/releases/download/{tag}/snap-ocr-{ver}-macos.zip",
        "sha256": sha,
        "size": int(size),
    },
}
with open("dist/appcast.json", "w", encoding="utf-8") as f:
    json.dump(appcast, f, ensure_ascii=False, indent=2)
print(f"📝 appcast: v{ver} min_required={min_req or '无'} notes={len(notes)} 条")
PY

# 4. 创建 release（--latest 保证 releases/latest 直链指向它）
gh release create "$TAG" "$ZIP" dist/appcast.json \
  --title "snap-ocr $TAG" --notes-file "$NOTES_FILE" --latest $DRAFT

# 5. 自检：appcast 直链可匿名访问
if [ -n "$DRAFT" ]; then
  echo "⚠️  草稿模式：asset 直链未公开，正式发布后客户端才可见"
else
  CODE=$(curl -sL -o /dev/null -w '%{http_code}' \
    "https://github.com/$REPO/releases/latest/download/appcast.json")
  [ "$CODE" = "200" ] || { echo "❌ appcast 自检失败 HTTP $CODE"; exit 1; }
  echo "✅ 已发布: https://github.com/$REPO/releases/tag/$TAG"
  [ -n "$MIN_REQUIRED" ] && echo "🚨 低于 v$MIN_REQUIRED 的客户端将被强制更新"
fi
