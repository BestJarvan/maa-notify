# MAA 状态监控（maa-notify）

读取本机 MAA 的日志，判断每天 07:02 / 19:01 两次定时任务是否成功、哪些任务失败、失败原因，并通过飞书自定义机器人 Webhook 推送卡片。

**默认只读 MAA**：分析日志、判定任务、推送飞书，不操作游戏，不修改 MAA 的配置或日志。  
可选的“自动启停 MAA”会启动/退出 MAA；该功能默认关闭，启用后才会执行。

## 两种运行方式

### A. 菜单栏 App（推荐，无需 FDA）

`MaaNotify.app` 通过系统文件选择框只获得 MAA `Data` 目录的访问权，把日志镜像到：

```text
~/Library/Application Support/MaaNotify/feed/
```

然后由独立的 Python watcher 读取镜像并推送飞书。它不需要“完全磁盘访问权限”，适合开机自启。

```bash
cd /path/to/maa-notify
./install_menuapp.sh
```

第一次运行会弹出目录选择框，选择：

```text
~/Library/Containers/com.hguandl.MeoAsstMac/Data
```

菜单栏 MAA 图标里可以：

- 查看同步状态、上次结果
- 更换菜单栏图标（天线 / 手柄 / 盾牌 / 闪电 / 波形 / MAA 文字 / MAA App 图标）
- 重新选择 MAA 目录
- 打开 watcher 日志 / feed / 配置
- 重启监控
- 开启/关闭飞书推送（默认关闭，即 dry-run）

菜单栏版使用独立状态：

```text
~/Library/Application Support/MaaNotify/menuapp/state/
```

不会影响原来的手动 watcher 或 `~/Library/Application Support/MaaNotify/state.json`。

卸载：

```bash
./uninstall_menuapp.sh          # 保留 feed/state
./uninstall_menuapp.sh --purge  # 一起删除
```

### B. 命令行 / LaunchAgent（旧方式）

如果你已经手动启动了 watcher，或者想用原来的 launchd 方式，继续看下面的 `install.sh`。  
注意：这条路径需要给 Python 授予“完全磁盘访问权限”，在部分 macOS 版本上可能不生效。

## 它如何判定

- 以 `gui.log` 为主：识别 `开始任务：X`、`完成任务：X`、`任务出错：X`、`任务已全部完成！`、`已停止`、`连接失败`、`StartTasksFailed`。
- 以 `asst.log` / `asst.bak.log` 为辅：提取 `[ERR]` 行作为失败原因，例如 `StageResource` → “关卡识别失败”。
- 读取 `DailyTasks/Default.plist` 的任务顺序，解决多个“刷理智”同名问题（按配置顺序映射到具体关卡，如 `PR-A-1`、`PR-C-1`、`1-7`）。
- 不会把 `任务已全部完成！` 当作成功：历史日志里出现过所有任务报错、仍然打印该行的情况。成功必须由逐任务完成记录推导。
- `完成任务：访问好友` 这类子任务不会当成顶层任务。
- `关闭游戏` 默认不纳入完成率：当前 GUI 版本不打印它的任务日志，但核心会隐式执行。如需要，把 `policy.include_closedown` 改为 `true`。

## 状态含义

| 状态 | 含义 |
|---|---|
| ✅ 运行成功 | 配置中的任务全部完成，无最终错误 |
| ⚠️ 部分任务失败 | 运行正常结束，但有任务失败/未执行 |
| ❌ 运行失败 | 连接失败、启动失败，或没有任何有效任务完成 |
| 🛑 已取消 | 运行中出现 `已停止` |
| ⏳ 超时/卡死 | 超过硬上限，或长时间无日志增长 |
| ❓ 未检测到运行 | 时间窗内没有日志，MAA 未启动/电脑休眠 |

PR-A-1 每天都会失败、随后 1-7 成功，所以正常一天的状态是“部分任务失败”。如果确认 PR-A-1 可忽略，在 `config.json` 里配置：

```json
"policy": {
  "optional_tasks": ["刷理智（PR-A-1）"]
}
```

这样整体仍算成功，但卡片里仍会保留该任务的提示。

## 安装

```bash
cd /path/to/maa-notify
./install.sh
```

安装脚本会：

1. 复制脚本到 `~/Library/Application Support/MaaNotify/app`（可用 `MAA_NOTIFY_HOME` 改路径）。  
   不放在 `~/Documents`：launchd 用户代理没有“文件与文件夹”TCC 授权，会读不到脚本。
2. 创建 `config.json`（权限 600），首次安装需要填写飞书 Webhook。
3. 写入并加载 `~/Library/LaunchAgents/com.jarvan.maa-notify.plist`。
4. 启动 `watch` 守护进程。

## 配置飞书

1. 在飞书群中打开“群设置 → 群机器人 → 添加机器人 → 自定义机器人”。
2. 复制 Webhook 地址，填入 `config.json`：

```json
{
  "feishu": {
    "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx",
    "secret": "",
    "keyword": "MAA",
    "notify_on_success": true,
    "notify_on_partial": true,
    "notify_on_missed": true
  }
}
```

- 如果机器人开启了“签名校验”，把密钥填到 `secret`。
- 如果设置了“自定义关键词”，确保 `keyword` 出现在卡片里（默认 `MAA`）。
- Webhook 是敏感信息，`config.json` 已设为 600，不要提交到 Git。

修改配置后重启守护进程：

```bash
launchctl kickstart -k gui/$(id -u)/com.jarvan.maa-notify
```

## 验证

```bash
APP="$HOME/Library/Application Support/MaaNotify/app"

# 发一张测试卡片
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" test

# 回放今天 07:02 的真实日志（只打印，不发飞书）
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" --dry-run replay --date 2026-11-23 --slot 07:02

# 回放并发送
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" replay --date 2026-11-23 --slot 07:02 --send

# 查看当前状态
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" status

# 检查权限与配置
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" doctor
```

## 常用命令

```bash
APP="$HOME/Library/Application Support/MaaNotify/app"

# 运行状态（JSON，给菜单栏/脚本用）
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" status --json

# 回放任意时间段
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" --dry-run replay \
  --from "2026-07-22 11:53" --to "2026-07-22 12:03"

# 停止 / 卸载（保留数据）
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jarvan.maa-notify.plist

# 彻底删除
"$APP/uninstall.sh" --purge
```

## 文件位置

```text
~/Applications/MaaNotify.app/                       # 菜单栏 App
~/Library/Application Support/MaaNotify/app/         # Python 运行时
~/Library/Application Support/MaaNotify/menuapp/     # 菜单栏版配置/书签/状态
~/Library/Application Support/MaaNotify/feed/        # 镜像后的日志
~/Library/LaunchAgents/com.jarvan.maa-notify.menu.plist
~/Library/Application Support/MaaNotify/state.json   # 当前状态/偏移/去重
~/Library/Application Support/MaaNotify/runs.jsonl   # 历史报告
~/Library/Logs/MaaNotify/maa-notify.log              # 自身日志
~/Library/Logs/MaaNotify/launchd.out.log
~/Library/Logs/MaaNotify/launchd.err.log
```

## 自动启停 MAA

菜单栏版可以托管 MAA 的启停，默认关闭；打开后：

- 每天 **06:57 / 18:56**（定时前 5 分钟）检查 MAA：
  - 不在运行 → 自动 `open -a /Applications/MAA.app`
  - 已在运行 → 不重复启动
- 每场任务结束、报告发送后 **60 秒**：
  - 自动退出 MAA（AppleScript，失败则 SIGTERM）
  - 下一场前再自动启动

菜单栏菜单里可以直接切换 **“开启/关闭自动启停 MAA”**。  
`dry-run` 阶段不会实际启停 MAA，避免影响手动监控。

对应配置：

```json
"policy": {
  "auto_start_maa": true,
  "auto_quit_maa": true,
  "auto_start_lead_minutes": 5,
  "auto_quit_delay_seconds": 60
}
```

注意：如果 Mac 在 06:57 处于睡眠，定时器和自动启动都不会执行。

## 参数说明

| 参数 | 默认 | 说明 |
|---|---|---|
| `policy.grace_minutes` | 30 | 超过定时多久仍无运行，发“未检测到运行” |
| `policy.idle_timeout_minutes` | 15 | 多久没有日志增长判定卡死 |
| `policy.hard_timeout_minutes` | 180 | 单场运行硬上限（实际观察到 38–66 分钟） |
| `policy.optional_tasks` | `[]` | 可忽略失败的任务显示名，例如 `刷理智（PR-A-1）` |
| `policy.include_closedown` | `false` | 是否把“关闭游戏”纳入完成率 |
| `feishu.notify_on_success` | `true` | 成功是否推送 |
| `feishu.notify_on_partial` | `true` | 部分失败是否推送 |
| `policy.auto_start_maa` | `false` | 定时前自动启动 MAA |
| `policy.auto_quit_maa` | `false` | 每场结束后自动退出 MAA |
| `policy.auto_start_lead_minutes` | `5` | 提前多少分钟启动 MAA |
| `policy.auto_quit_delay_seconds` | `60` | 报告发送后多少秒退出 MAA |

## 故障排查

### 没有收到消息

```bash
tail -100 ~/Library/Logs/MaaNotify/maa-notify.log
tail -50 ~/Library/Logs/MaaNotify/launchd.err.log
APP="$HOME/Library/Application Support/MaaNotify/app"
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" status
```

- `config.json` 里 webhook 仍是 `REPLACE_ME`：守护进程会进入 dry-run，只写日志不发消息。
- 日志出现“飞书推送失败”：检查 Webhook、签名密钥、关键词、网络。
- 失败消息会进入 `pending` 队列，守护进程每 60 秒重试一次。

### MAA 升级后解析异常

先跑 `test` 和 `replay`。如果 GUI 日志格式变化，`gui.log` 中原始错误行会保留在卡片里；再根据新格式补解析规则和回归测试。

### 权限（重要）

MAA 的日志位于 sandbox 容器 `~/Library/Containers/com.hguandl.MeoAsstMac/...`。从终端运行脚本时通常能读，但 **launchd 用户代理默认读不到**，会报 `Operation not permitted`。这是 macOS TCC 的限制，不是 MAA 或脚本的问题。

需要给 Python 一次性授予“完全磁盘访问权限”：

```bash
APP="$HOME/Library/Application Support/MaaNotify/app"
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" doctor --open
```

1. 在打开的“完全磁盘访问权限”里点 `+`。
2. 按 `Cmd+Shift+G`，选择 `doctor` 输出的 `Python.app` 路径：
   `/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app`
3. 打开开关。
4. 重启代理并验证：

```bash
launchctl kickstart -k gui/$(id -u)/com.jarvan.maa-notify
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" doctor
```

`doctor` 显示 `MAA 日志权限：OK` 后，07:02 / 19:01 的监控才会生效。安装脚本也会用 `launchctl submit` 实测一次，并打印同样的提示。

## 上传 Git

仓库里不包含 `config.json`、日志、构建产物和状态文件，Webhook 不会泄露。

```bash
cd /path/to/maa-notify
git init
git add .
git commit -m "Initial MAA status monitor"
```

首次运行前复制配置并填入 Webhook：

```bash
cp config.example.json config.json
chmod 600 config.json
```

## 开发与测试

源码仓库（工作区）里运行：

```bash
cd /path/to/maa-notify
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -v
```

测试覆盖：同名刷理智映射、单任务重试、全部报错、连接失败、取消、子任务误判、`已停止` 收尾、真实日志回放、飞书卡片结构。
