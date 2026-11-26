#!/bin/bash
# 构建可分发 zip：MaaNotify-1.0.0.zip
# 包内包含预构建 App、Python 脚本、配置模板和安装脚本，不需要 Xcode。
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd -P)"
VERSION="1.0.0"
DIST="${1:-$SRC/dist}"
BUILD="$SRC/menuapp/build"
ROOT="$DIST/MaaNotify-$VERSION"
ZIP="$DIST/MaaNotify-$VERSION.zip"

bash "$SRC/menuapp/build.sh" "$BUILD"

rm -rf "$ROOT"
mkdir -p "$ROOT/menuapp/build"
ditto "$BUILD/MaaNotify.app" "$ROOT/menuapp/build/MaaNotify.app"
cp "$SRC/install_menuapp.sh" "$ROOT/"
cp "$SRC/maa_notify.py" "$ROOT/"
cp "$SRC/config.example.json" "$ROOT/"
cp "$SRC/README.md" "$ROOT/"
cp "$SRC/LICENSE" "$ROOT/"

rm -f "$ZIP"
ditto -c -k --norsrc --keepParent "$ROOT" "$ZIP"
shasum -a 256 "$ZIP" > "$ZIP.sha256"

echo "打包完成：$ZIP"
echo "校验：$ZIP.sha256"
