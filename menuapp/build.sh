#!/bin/bash
# 构建 MaaNotify.app（无需 Xcode 工程）
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd -P)"
OUT="${1:-$SRC/build}"
APP="$OUT/MaaNotify.app"
VERSION="${MAA_NOTIFY_VERSION:-$(cat "$SRC/../VERSION" 2>/dev/null || echo 1.0.0)}"
BUILD_NUMBER="${MAA_NOTIFY_BUILD:-1}"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "编译 Swift…"
swiftc -O -swift-version 5 -framework Cocoa -framework SwiftUI \
  -o "$APP/Contents/MacOS/MaaNotify" \
  "$SRC/MaaNotifyMenu.swift" "$SRC/SettingsView.swift" "$SRC/MenuPanelView.swift"

cp "$SRC/Info.plist" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$APP/Contents/Info.plist"
/usr/bin/plutil -lint "$APP/Contents/Info.plist" >/dev/null

echo "签名（ad-hoc）…"
codesign --force --deep --sign - "$APP" >/dev/null

echo "构建完成：$APP"
