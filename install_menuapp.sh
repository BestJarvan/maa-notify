#!/bin/bash
# 安装菜单栏版：MaaNotify.app + LaunchAgent。
# 不会停止当前手动 watcher，也不会修改它的 state。
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd -P)"
APP_DEST="${MAA_NOTIFY_MENU_APP:-$HOME/Applications/MaaNotify.app}"
BUILD="$SRC/menuapp/build"
LABEL="com.jarvan.maa-notify.menu"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_VALUE="$(id -u)"
SUPPORT="$HOME/Library/Application Support/MaaNotify"

echo "源码：$SRC"
echo "App：$APP_DEST"

# 1. 构建（发布包中可能只有预构建 App，没有 build.sh）
if [ -f "$SRC/menuapp/build.sh" ]; then
  bash "$SRC/menuapp/build.sh" "$BUILD"
else
  echo "使用预构建 App：$BUILD/MaaNotify.app"
fi

# 2. 安装 App
mkdir -p "$HOME/Applications"
rm -rf "$APP_DEST"
ditto "$BUILD/MaaNotify.app" "$APP_DEST"
codesign --force --deep --sign - "$APP_DEST" >/dev/null

# 3. 确保 Python 运行时是最新的（不影响已经在跑的进程）
mkdir -p "$SUPPORT/app"
cp -f "$SRC/maa_notify.py" "$SUPPORT/app/maa_notify.py"
cp -f "$SRC/config.example.json" "$SUPPORT/app/config.example.json" 2>/dev/null || true
cp -f "$SRC/README.md" "$SUPPORT/app/README.md" 2>/dev/null || true
cp -f "$SRC/LICENSE" "$SUPPORT/app/LICENSE" 2>/dev/null || true
chmod +x "$SUPPORT/app/maa_notify.py"

# 首次安装必须有一个可编辑的配置；菜单栏 App 会从它同步 feishu 配置。
if [ ! -f "$SUPPORT/app/config.json" ]; then
  if [ -f "$SRC/config.json" ]; then
    cp -f "$SRC/config.json" "$SUPPORT/app/config.json"
  else
    cp -f "$SRC/config.example.json" "$SUPPORT/app/config.json"
  fi
  chmod 600 "$SUPPORT/app/config.json"
  echo "已创建 $SUPPORT/app/config.json，请填写 feishu.webhook。"
fi

# 4. LaunchAgent
# 升级安装时保留用户之前选择的“登录时自动启动”状态。
mkdir -p "$HOME/Library/LaunchAgents" "$SUPPORT/menuapp" "$SUPPORT/feed"
LOGIN_ITEM="true"
if [ -f "$PLIST" ]; then
  PREV_LOGIN_ITEM="$(/usr/bin/plutil -extract RunAtLoad raw -o - "$PLIST" 2>/dev/null || true)"
  if [ "$PREV_LOGIN_ITEM" = "false" ]; then
    LOGIN_ITEM="false"
  fi
fi
KEEPALIVE=""
if [ "$LOGIN_ITEM" = "true" ]; then
  KEEPALIVE='  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key><false/>
  </dict>'
fi
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP_DEST/Contents/MacOS/MaaNotify</string>
  </array>
  <key>RunAtLoad</key><$LOGIN_ITEM/>
$KEEPALIVE
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>ProcessType</key><string>Interactive</string>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$SUPPORT/menuapp/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$SUPPORT/menuapp/launchd.err.log</string>
</dict>
</plist>
EOF
/usr/bin/plutil -lint "$PLIST" >/dev/null

launchctl enable "gui/$UID_VALUE/$LABEL" 2>/dev/null || true
launchctl bootout "gui/$UID_VALUE" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_VALUE" "$PLIST"
# 即使用户关闭了登录自启，安装/升级后也先启动一次，方便继续配置。
launchctl kickstart -k "gui/$UID_VALUE/$LABEL" 2>/dev/null || true

sleep 2
echo
echo "安装完成。"
echo "菜单栏出现 MAA 图标后，第一次会要求选择 MAA 的 Data 目录："
echo "  ~/Library/Containers/com.hguandl.MeoAsstMac/Data"
echo
echo "首次安装默认实际执行；自动启停默认关闭，机器人不填也能监控。"
if [ "$LOGIN_ITEM" = "true" ]; then
  echo "登录时自动启动：开启（可在设置窗口关闭）。"
else
  echo "登录时自动启动：已保留之前的关闭设置（可在设置窗口重新开启）。"
fi
echo "配置文件：$SUPPORT/app/config.json"
echo "日志：$SUPPORT/menuapp/watcher.log"
echo "卸载：$SRC/uninstall_menuapp.sh"
