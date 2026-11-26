# MAA Notify

一个运行在 macOS 上的 **MAA（MaaAssistantArknights）运行状态监控**。

它会读取 MAA 的日志，判断定时任务的执行结果、哪些任务失败、失败原因，并通过消息机器人推送。默认支持飞书 / Lark，也支持通用 Webhook、Bark、钉钉、企业微信。附带的菜单栏 App 可以在不需要“完全磁盘访问权限”的情况下读取 MAA 的 sandbox 日志。

> 默认只读 MAA：不操作游戏，不修改 MAA 配置或日志。  
> “自动启停 MAA”是可选功能，默认关闭；启用后才会启动/退出 MAA。

---

## 功能

- 逐任务判定，而不是只看“任务已全部完成”：
  - 开始唤醒、自动公招、基建换班、刷理智、收取信用及购物、领取奖励、更换主题……
  - 同名“刷理智”按配置顺序区分为 `PR-A-1`、`PR-C-1`、`1-7` 等具体实例。
- 区分“失败”和“重试后成功”，避免把临时错误误报成整体失败。
- 从 `asst.log` 提取错误原因，例如：
  - `StageResource` → 关卡识别失败
  - `connectFailed` → 连接模拟器失败
  - `StartTasksFailed` → 启动任务失败
- 消息推送：
  - 飞书 / Lark 交互卡片；
  - 通用 Webhook（JSON）；
  - Bark、钉钉、企业微信；
  - 成功 / 部分失败 / 失败 / 取消 / 超时 / 未运行。
- macOS 菜单栏 App：
  - 文件选择框只授权 MAA 的 `Data` 目录，**不需要 FDA**；
  - 日志镜像到 App 自己的目录，Python watcher 读镜像；
  - 菜单栏查看同步状态、上次结果、日志；
  - 更换图标、重新选择目录、开启/关闭消息推送。
- 可选自动启停 MAA：
  - 定时前 5 分钟检查，没运行就启动；
  - 每场报告发送后 60 秒退出 MAA；
  - 下一场前再启动。
- 无第三方 Python 依赖，只用标准库。
- 27 个回归测试。

---

## 工作方式

```text
MAA.app（07:02 / 19:01）
   │
   ├─ gui.log   任务开始/完成/出错
   └─ asst.log  底层错误原因
          │
          ▼
MaaNotify.app（菜单栏，LSUIElement）
   │  ① 用户选择 MAA Data 目录，保存 security-scoped bookmark
   │  ② 每 2 秒把日志增量镜像到 feed/
   │  ③ 启动并守护 Python watcher
          │
          ▼
Python watcher
   ├─ 读 feed/gui.log、feed/asst.log
   ├─ 按 Default.plist 顺序映射任务实例
   ├─ 状态机判定
   └─ 消息机器人 Webhook（飞书 / 通用 / Bark / 钉钉 / 企业微信）
```

为什么需要镜像：MAA 的日志在 `~/Library/Containers/com.hguandl.MeoAsstMac/...`，launchd 后台进程默认读不到。菜单栏 App 通过用户选择目录获得访问权，把日志复制到 `~/Library/Application Support/MaaNotify/feed/`，Python 只读镜像，因此不需要给 Python 开“完全磁盘访问权限”。

---

## 快速开始

### 环境要求

- macOS 13 或更高版本
- 已安装 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights)（`/Applications/MAA.app`）
- 任意一种消息机器人 Webhook（飞书 / Lark、通用 Webhook、Bark、钉钉、企业微信）
- Python 3.9+（macOS 自带 `/usr/bin/python3` 即可）
- 构建菜单栏 App 需要 Xcode Command Line Tools（`xcode-select --install`）

### 1. 克隆

```bash
git clone https://github.com/BestJarvan/maa-notify.git
cd maa-notify
```

### 2. 安装菜单栏版

```bash
./install_menuapp.sh
```

安装脚本会：

1. 编译并安装 `~/Applications/MaaNotify.app`；
2. 部署 LaunchAgent `com.jarvan.maa-notify.menu`；
3. 创建配置：
   ```text
   ~/Library/Application Support/MaaNotify/app/config.json
   ```
4. 首次启动时弹出目录选择框；如果还没配置 Webhook，会自动打开设置窗口。

### 3. 首次授权目录

第一次运行时选择：

```text
~/Library/Containers/com.hguandl.MeoAsstMac/Data
```

App 只会读取这个目录，不需要“完全磁盘访问权限”。  
如果之后系统让书签失效，菜单栏会提示重新选择一次。

### 4. 在设置窗口配置

点菜单栏 MAA 图标 → **设置…**，不用手改 JSON。

**消息机器人**

1. 选择机器人类型：飞书 / Lark、通用 Webhook、Bark、钉钉、企业微信；
2. 粘贴对应的 Webhook 地址；
3. 需要签名校验的机器人（飞书 / 钉钉）填写 `secret`；
4. 设置了关键词的机器人填写 `keyword`；
5. 点击 **发送测试消息** 验证；
6. 打开 **启用消息推送**，点 **保存并应用**。

不同机器人收到的格式不同：

- 飞书：交互卡片；
- 通用 Webhook：包含 `title`、`content`、`status`、`slot`、`report` 的 JSON；
- Bark：`title` + `body`；
- 钉钉 / 企业微信：Markdown。

**定时与自动启停**

设置窗口里“监控定时”是一个可增删的时间数组，例如：

```text
07:02
19:01
```

这个数组决定软件何时检测/启动 MAA。MAA 自己的定时任务在“MAA 当前定时”里显示，两者可以不同；如果希望 MAA 也按新时间执行，点 **写入 MAA 定时…**。

可以设置：

- 定时前自动启动 MAA；
- 任务结束后自动退出 MAA；
- 提前几分钟启动；
- 报告发送后多少秒退出。

**MAA 目录**

可以重新选择目录、打开目录，或手动启动/退出 MAA。

### 5. 验证

在设置窗口点 **发送测试消息** 即可，不需要开终端。

也可以命令行验证：

```bash
APP="$HOME/Library/Application Support/MaaNotify/app"
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" test
```

---

## 菜单栏 App

安装后菜单栏会出现一个图标（默认天线，可在设置里更换）。

菜单只保留最关键的信息和入口：

```text
MAA 监控 · 运行中
上次：07:02 部分失败（7/8）
定时：07:02 / 19:01
————————————
设置…
打开日志
————————————
退出
```

所有配置都收进 **设置窗口**：

| 区域 | 内容 |
|---|---|
| 消息机器人 | 类型（飞书/通用/Bark/钉钉/企业微信）、Webhook、secret、keyword、启用推送、测试 |
| 定时与自动启停 | 监控定时数组、MAA 当前定时、写入 MAA、提前启动、退出延迟 |
| MAA 目录 | 重新选择、打开目录、启动/退出 MAA |
| 外观 | 天线/手柄/盾牌/闪电/波形/MAA 文字/MAA App 图标 |
| 高级 | 打开 JSON 配置、查看状态 |

首次配置会显示“三步开始监控”引导：选择目录 → 粘贴 Webhook → 发送测试消息。

需要直接打开设置窗口时：

```bash
open -a "$HOME/Applications/MaaNotify.app" --args --settings
```

日志：

```text
~/Library/Application Support/MaaNotify/menuapp/watcher.log
~/Library/Application Support/MaaNotify/menuapp/launchd.err.log
```

---

## 自动启停 MAA

在 **设置 → 定时与自动启停** 开启，或在配置中设置：

```json
"maa": {
  "schedule_times": ["07:02", "19:01"]
},
"policy": {
  "auto_start_maa": true,
  "auto_quit_maa": true,
  "auto_start_lead_minutes": 5,
  "auto_quit_delay_seconds": 60
}
```

“监控定时”可以不同于 MAA 自己的定时。改完后如果希望 MAA 也在新时间执行，使用设置窗口的 **写入 MAA 定时…**；这会修改 MAA 的 `MAAScheduledDailyTaskTimer`，可能退出并重启 MAA。

行为：

```text
06:57  检查 MAA，没运行则 open -a /Applications/MAA.app
07:02  MAA 定时执行
07:4x  报告发送后 60 秒，自动退出 MAA
18:56  再次检查/启动
19:01  MAA 定时执行
19:2x  自动退出 MAA
```

### 修改 MAA 自己的定时

软件**可以**修改 MAA 的 `MAAScheduledDailyTaskTimer`，但不是自动的：

1. 在设置窗口的“监控定时”里增删时间；
2. 点 **写入 MAA 定时…**；
3. 确认弹窗；
4. 软件会先退出 MAA（如果正在运行），写入定时，再重新启动 MAA。

写入前会备份旧值：

```text
~/Library/Application Support/MaaNotify/maa-schedule-backup.json
```

也可以用 CLI：

```bash
APP="$HOME/Library/Application Support/MaaNotify/app"

# 查看当前生效的监控定时
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" schedule

# 写入 MAA 定时（会让 MAA 退出再启动）
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json"   sync-maa-schedule --times "07:02,19:01"
```

注意：这会改动 MAA 的偏好设置；如果 MAA 正在执行任务，会被中断。不要在任务进行中操作。

注意：

- 只有 live 模式会启停 MAA；dry-run 不会。
- 如果 Mac 在 06:57 / 18:56 处于睡眠，自动启动不会执行。
- MAA 更新通常 1 分钟左右，5 分钟提前量一般足够；如果更新弹窗需要手动确认，可能跨过定时，监控会发“未检测到运行”卡片。
- 自动退出优先使用 AppleScript，失败时用 `SIGTERM` 兜底。

---

## 判定规则

### 任务实例

MAA 的 `Default.plist` 里可能有多个同名“刷理智”。工具按配置顺序映射：

```text
刷理智（PR-A-1）
刷理智（PR-C-1）
刷理智（1-7）
```

即使 GUI 日志里都写成 `开始任务：刷理智`，也会分别归属到对应关卡。

### 状态

| 状态 | 含义 |
|---|---|
| ✅ 运行成功 | 配置中的任务全部完成，无最终错误 |
| ⚠️ 部分任务失败 | 运行正常结束，但有任务失败/未执行 |
| ❌ 运行失败 | 连接失败、启动失败，或没有任何有效任务完成 |
| 🛑 已取消 | 运行中出现 `已停止` |
| ⏳ 超时/卡死 | 超过硬上限，或长时间无日志增长 |
| ❓ 未检测到运行 | 时间窗内没有日志，MAA 未启动/电脑休眠 |

### 不依赖“任务已全部完成”

历史日志里出现过所有任务报错、GUI 仍打印 `任务已全部完成！` 的情况。  
因此成功必须由逐任务完成记录推导，不能只看最后一行。

### 子任务

`完成任务：访问好友` 是“收取信用及购物”里的子步骤，不会被当成独立顶层任务。

### 关闭游戏

当前 GUI 版本不打印 `CloseDown` 的任务日志，但核心会隐式执行。  
默认不把“关闭游戏”纳入完成率；如需要可设置：

```json
"policy": {
  "include_closedown": true
}
```

### PR-A-1 每天失败

如果你的 MAA 配置里 PR-A-1 每天都会失败、随后 1-7 成功，整体会显示“部分任务失败”。  
如果确认可忽略：

```json
"policy": {
  "optional_tasks": ["刷理智（PR-A-1）"]
}
```

整体仍算成功，但卡片里保留该任务提示。

---

## 消息卡片示例（飞书）

```text
⚠️ MAA 07:02 部分任务失败
07:02 – 07:40（38m24s）｜7/8 完成

✅ 开始唤醒        28s
✅ 自动公招        36s
✅ 基建换班        7m30s
❌ 刷理智（PR-A-1）：关卡识别失败
✅ 刷理智（1-7）   27m38s
✅ 收取信用及购物  1m11s
✅ 领取奖励        16s
✅ 更换主题        20s

失败任务：刷理智（PR-A-1）
理智：101 → 4
```

---

## 配置说明

`config.json` 与 `config.example.json` 结构一致。  
旧版的 `feishu` 配置仍然兼容；新配置使用 `notify`。

```json
{
  "notify": {
    "provider": "feishu",
    "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/REPLACE_ME",
    "secret": "",
    "keyword": "MAA",
    "headers": {},
    "body_template": "",
    "notify_on_success": true,
    "notify_on_partial": true,
    "notify_on_missed": true
  },
  "maa": {
    "schedule_times": ["07:02", "19:01"]
  },
  "policy": {
    "grace_minutes": 30,
    "idle_timeout_minutes": 15,
    "hard_timeout_minutes": 180,
    "poll_seconds": 2,
    "optional_tasks": [],
    "include_closedown": false,
    "auto_start_maa": false,
    "auto_quit_maa": false,
    "auto_start_lead_minutes": 5,
    "auto_quit_delay_seconds": 60
  },
  "runtime": {
    "dry_run": false
  }
}
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `notify.provider` | `feishu` | `feishu` / `generic` / `bark` / `dingtalk` / `wecom` |
| `notify.webhook` | 空 | 机器人 Webhook |
| `notify.secret` | 空 | 飞书/钉钉签名密钥 |
| `notify.keyword` | `MAA` | 机器人关键词 |
| `notify.headers` | `{}` | 通用 Webhook 自定义请求头 |
| `notify.body_template` | 空 | 通用 Webhook 自定义 JSON 模板 |
| `notify.notify_on_*` | `true` | 成功/部分失败/未运行是否推送 |
| `maa.schedule_times` | `[]` | 监控定时数组；为空则读 MAA |
| `policy.auto_start_maa` | `false` | 定时前自动启动 MAA |
| `policy.grace_minutes` | `30` | 定时后多久仍无运行判“未检测到” |
| `policy.idle_timeout_minutes` | `15` | 多久无日志增长判卡死 |
| `policy.hard_timeout_minutes` | `180` | 单场运行硬上限 |
| `policy.optional_tasks` | `[]` | 可忽略失败的任务显示名 |
| `policy.include_closedown` | `false` | 是否把关闭游戏纳入完成率 |
| `policy.auto_quit_maa` | `false` | 每场结束后自动退出 MAA |
| `policy.auto_start_lead_minutes` | `5` | 提前多少分钟启动 |
| `policy.auto_quit_delay_seconds` | `60` | 报告后多少秒退出 |
| `runtime.dry_run` | `true`（菜单栏首次） | 只写日志，不发消息 |

---

## CLI

```bash
APP="$HOME/Library/Application Support/MaaNotify/app"
CFG="$HOME/Library/Application Support/MaaNotify/menuapp/config.json"
STATE="$HOME/Library/Application Support/MaaNotify/menuapp/state"

# 当前状态
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$CFG" --state-dir "$STATE" status

# JSON 状态
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$CFG" --state-dir "$STATE" status --json

# 回放今天 07:02
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$CFG" --state-dir "$STATE" --dry-run replay \
  --date 2026-11-23 --slot 07:02

# 回放并发送
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$CFG" --state-dir "$STATE" replay \
  --date 2026-11-23 --slot 07:02 --send

# 测试消息机器人
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" test

# 查看/写入定时
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" schedule
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json"   sync-maa-schedule --times "07:02,19:01"

# 检查权限/配置
/usr/bin/python3 -B "$APP/maa_notify.py" --config "$APP/config.json" doctor
```

---

## 目录结构

```text
maa-notify/
├── maa_notify.py             # Python 解析/状态机/通知/CLI
├── config.example.json       # 配置模板
├── install_menuapp.sh        # 菜单栏版安装
├── uninstall_menuapp.sh      # 菜单栏版卸载
├── install.sh                # 旧 LaunchAgent 安装（需要 FDA）
├── uninstall.sh
├── menuapp/
│   ├── MaaNotifyMenu.swift   # 菜单栏 App / 主进程
│   ├── SettingsView.swift    # 设置窗口
│   ├── Info.plist
│   └── build.sh
├── tests/
│   └── test_maa_notify.py
├── LICENSE
└── README.md
```

运行数据：

```text
~/Applications/MaaNotify.app
~/Library/Application Support/MaaNotify/app/          # Python 运行时与基础配置
~/Library/Application Support/MaaNotify/menuapp/      # 书签、镜像状态、watcher 日志
~/Library/Application Support/MaaNotify/feed/         # 镜像后的 MAA 日志
~/Library/LaunchAgents/com.jarvan.maa-notify.menu.plist
```

---

## 故障排查

### 菜单栏没有 MAA 图标

```bash
launchctl print gui/$(id -u)/com.jarvan.maa-notify.menu
tail -30 "$HOME/Library/Application Support/MaaNotify/menuapp/launchd.err.log"
```

### 没有收到消息

```bash
# 确认 dry_run=false
python3 -B -c "import json,pathlib; print(json.loads((pathlib.Path.home()/'Library/Application Support/MaaNotify/menuapp/config.json').read_text())['runtime'])"

# 看 watcher
tail -50 "$HOME/Library/Application Support/MaaNotify/menuapp/watcher.log"
```

- Webhook 还是 `REPLACE_ME`：处于 dry-run。
- 日志出现“消息推送失败”：检查机器人类型、Webhook、签名、关键词、网络。
- 失败消息会进入 `pending` 队列，每 60 秒重试。

### 提示 `Operation not permitted`

菜单栏版不需要给 Python FDA。如果你运行的是旧的 `install.sh`/LaunchAgent 版本，需要给 Python.app 完全磁盘访问权限；或者直接改用 `install_menuapp.sh`。

### 菜单栏要求重新选择目录

系统让 security-scoped bookmark 失效了。选择：

```text
~/Library/Containers/com.hguandl.MeoAsstMac/Data
```

即可。

### 自动启停不生效

- 确认设置窗口里“定时前自动启动 MAA”和“任务结束后自动退出 MAA”已打开。
- 确认当前不是 dry-run。
- 06:57 / 18:56 时 Mac 不能处于睡眠。
- MAA 路径应为 `/Applications/MAA.app`。

---

## 开发与测试

```bash
cd maa-notify
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -v
```

测试覆盖：

- 同名刷理智映射
- 单任务重试后成功
- 全部报错
- 连接失败
- 取消 / 已停止
- 子任务误判
- 资源更新不触发运行开始
- 自动启动时间窗
- 自动退出状态
- 消息卡片/通用 payload 结构
- 定时数组与 MAA 定时覆盖
- 真实日志回放（本机日志不存在时自动跳过）

构建菜单栏 App：

```bash
cd menuapp
./build.sh
open build/MaaNotify.app
```

---

## 安全与隐私

- `config.json` 不在 Git 中，权限 600。
- Webhook 和签名密钥只保存在本地。
- 菜单栏 App 只获得用户选择的 MAA `Data` 目录访问权。
- Python watcher 只读镜像目录，不直接读 MAA 容器。
- 自动启停是可选功能，默认关闭；启用后才会启动/退出 MAA。
- 唯一外部网络请求是发送消息机器人 Webhook。

---

## 已知限制

- 仅支持 macOS。
- 依赖 MAA GUI 的日志格式；MAA 大版本升级后可能需要更新解析规则。
- 如果 MAA 更新弹窗需要手动确认、跨过定时时间，可能错过一场；监控会报告“未检测到运行”。
- Mac 睡眠期间定时器和自动启动都不会执行。
- 日志镜像目录会随运行时间增长，可按需手动清理 `feed/`（不影响源码）。

---

## License

[MIT](LICENSE) © 2026 BestJarvan
