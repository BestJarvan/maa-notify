#!/bin/bash
# 卸载 launchd 用户代理；默认保留配置、状态和历史。
set -euo pipefail

LABEL="com.jarvan.maa-notify"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_VALUE="$(id -u)"

launchctl bootout "gui/$UID_VALUE" "$PLIST" 2>/dev/null || true
rm -f "$PLIST"

echo "已停止并移除 $LABEL。"

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$HOME/Library/Application Support/MaaNotify"
  rm -rf "$HOME/Library/Logs/MaaNotify"
  echo "已删除状态、历史和日志。"
else
  echo "配置、状态和历史仍保留在 ~/Library/Application Support/MaaNotify。"
  echo "如需彻底删除，运行：$0 --purge"
fi
