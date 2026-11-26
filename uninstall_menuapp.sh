#!/bin/bash
# 卸载菜单栏版；默认保留 feed/state，方便回滚。
set -euo pipefail

LABEL="com.jarvan.maa-notify.menu"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP_DEST="${MAA_NOTIFY_MENU_APP:-$HOME/Applications/MaaNotify.app}"
SUPPORT="$HOME/Library/Application Support/MaaNotify"
UID_VALUE="$(id -u)"

launchctl bootout "gui/$UID_VALUE" "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
rm -rf "$APP_DEST"

# 停掉可能残留的菜单栏版 watcher（不碰手动 watcher）
if [ -f "$SUPPORT/menuapp/watcher.pid" ]; then
  PID="$(cat "$SUPPORT/menuapp/watcher.pid" 2>/dev/null || true)"
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
  fi
  rm -f "$SUPPORT/menuapp/watcher.pid"
fi

echo "已移除菜单栏 App 和 LaunchAgent。"

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$SUPPORT/menuapp" "$SUPPORT/feed"
  echo "已删除菜单栏版的 feed/state（手动 watcher 的 state 未动）。"
else
  echo "菜单栏版的 feed/state 仍保留；彻底删除加 --purge。"
fi
