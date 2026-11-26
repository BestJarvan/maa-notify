#!/bin/bash
# 安装 MAA 状态监控：复制到 ~/Documents/maa-notify，并加载 launchd 用户代理。
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd -P)"
# 不要放在 ~/Documents：launchd 用户代理没有“文件与文件夹”TCC 授权，会读不到脚本。
DEST="${MAA_NOTIFY_HOME:-$HOME/Library/Application Support/MaaNotify/app}"
PY="${MAA_NOTIFY_PYTHON:-/usr/bin/python3}"
LABEL="com.jarvan.maa-notify"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_VALUE="$(id -u)"

echo "源目录：$SRC"
echo "安装到：$DEST"
echo "Python：$PY"

mkdir -p "$DEST" "$HOME/Library/LaunchAgents" \
  "$HOME/Library/Application Support/MaaNotify" \
  "$HOME/Library/Logs/MaaNotify"

if [ "$SRC" != "$DEST" ]; then
  cp -f "$SRC/maa_notify.py" "$DEST/maa_notify.py"
  cp -f "$SRC/config.example.json" "$DEST/config.example.json"
  cp -f "$SRC/README.md" "$DEST/README.md" 2>/dev/null || true
  cp -f "$SRC/install.sh" "$DEST/install.sh" 2>/dev/null || true
  cp -f "$SRC/uninstall.sh" "$DEST/uninstall.sh" 2>/dev/null || true
  mkdir -p "$DEST/tests"
  cp -f "$SRC/tests/test_maa_notify.py" "$DEST/tests/test_maa_notify.py" 2>/dev/null || true
fi

if [ ! -f "$DEST/config.json" ]; then
  cp "$DEST/config.example.json" "$DEST/config.json"
  chmod 600 "$DEST/config.json"
  echo "已创建 $DEST/config.json，请填写 feishu.webhook。"
fi

chmod +x "$DEST/maa_notify.py" "$DEST/install.sh" "$DEST/uninstall.sh" 2>/dev/null || true

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DEST/maa_notify.py</string>
    <string>--config</string>
    <string>$DEST/config.json</string>
    <string>watch</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$DEST</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONDONTWRITEBYTECODE</key>
    <string>1</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/MaaNotify/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/MaaNotify/launchd.err.log</string>
</dict>
</plist>
EOF

/usr/bin/plutil -lint "$PLIST" >/dev/null

launchctl bootout "gui/$UID_VALUE" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_VALUE" "$PLIST"
launchctl enable "gui/$UID_VALUE/$LABEL" 2>/dev/null || true

sleep 1
echo
echo "launchd 状态："
launchctl print "gui/$UID_VALUE/$LABEL" 2>/dev/null | sed -n '1,18p' || true
echo
echo "当前状态："
"$PY" -B "$DEST/maa_notify.py" --config "$DEST/config.json" status || true
echo

# launchd 用户代理没有继承终端的“完全磁盘访问权限”，用一次 submit 实测。
DOCTOR_OUT="$(mktemp -t maa-notify-doctor.XXXXXX)"
launchctl submit -l com.jarvan.maa-notify.doctor -o "$DOCTOR_OUT" -- \
  "$PY" "$DEST/maa_notify.py" --config "$DEST/config.json" doctor 2>/dev/null || true
sleep 3
echo "launchd 权限检查："
if grep -q "MAA 日志权限：DENIED" "$DOCTOR_OUT" 2>/dev/null; then
  echo "  DENIED —— launchd 还读不到 MAA 容器，需要给 Python 授予“完全磁盘访问权限”。"
  grep -E "Python.app|MAA 容器|路径：" "$DOCTOR_OUT" | sed 's/^/  /' || true
  echo
  echo "  打开设置：系统设置 → 隐私与安全性 → 完全磁盘访问权限"
  echo "  点 +，按 Cmd+Shift+G，选择上面的 Python.app，打开开关。"
  echo "  然后执行：launchctl kickstart -k gui/$(id -u)/com.jarvan.maa-notify"
  echo "  验证：$PY -B $DEST/maa_notify.py --config $DEST/config.json doctor"
else
  grep -E "MAA 日志权限|Python.app" "$DOCTOR_OUT" 2>/dev/null | sed 's/^/  /' || true
  echo "  OK"
fi
launchctl remove com.jarvan.maa-notify.doctor 2>/dev/null || true
rm -f "$DOCTOR_OUT"

echo
echo "安装完成。"
echo "下一步：编辑 $DEST/config.json，填入 feishu.webhook（可选 secret/keyword）。"
echo "验证： $PY -B $DEST/maa_notify.py --config $DEST/config.json test"
echo "日志： $HOME/Library/Logs/MaaNotify/maa-notify.log"
