#!/bin/bash
# 构建可分发产物：zip（install_menuapp.sh）+ dmg（安装.command）
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd -P)"
VERSION="${MAA_NOTIFY_VERSION:-$(cat "$SRC/VERSION")}"
DIST="${1:-$SRC/dist}"
BUILD="$SRC/menuapp/build"
ROOT="$DIST/MaaNotify-$VERSION"
ZIP="$DIST/MaaNotify-$VERSION.zip"
DMG="$DIST/MaaNotify-$VERSION.dmg"

MAA_NOTIFY_VERSION="$VERSION" bash "$SRC/menuapp/build.sh" "$BUILD"

rm -rf "$ROOT"
mkdir -p "$ROOT/menuapp/build"
ditto "$BUILD/MaaNotify.app" "$ROOT/menuapp/build/MaaNotify.app"
cp "$SRC/install_menuapp.sh" "$ROOT/"
cp "$SRC/安装.command" "$ROOT/"
cp "$SRC/maa_notify.py" "$ROOT/"
cp "$SRC/config.example.json" "$ROOT/"
cp "$SRC/README.md" "$ROOT/"
cp "$SRC/LICENSE" "$ROOT/"
chmod +x "$ROOT/install_menuapp.sh" "$ROOT/安装.command"

rm -f "$ZIP" "$ZIP.sha256" "$DMG" "$DMG.sha256"
ditto -c -k --norsrc --keepParent "$ROOT" "$ZIP"
shasum -a 256 "$ZIP" > "$ZIP.sha256"

# DMG 根目录直接放安装文件，挂载后双击“安装.command”即可。
hdiutil create \
  -volname "MaaNotify $VERSION" \
  -srcfolder "$ROOT" \
  -ov -format UDZO \
  "$DMG" >/dev/null
shasum -a 256 "$DMG" > "$DMG.sha256"

echo "打包完成："
echo "  $ZIP"
echo "  $DMG"
echo "校验："
echo "  $ZIP.sha256"
echo "  $DMG.sha256"
