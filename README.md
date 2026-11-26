# MAA Notify

macOS 上的 **MAA（MaaAssistantArknights）运行状态监控**。

读取 MAA 日志，判断每次定时任务是否成功、哪些任务失败、失败原因，可选通过消息机器人推送。附带菜单栏 App，不需要“完全磁盘访问权限”。

> 默认只读 MAA。可选的“自动启停 MAA”会操作 MAA，默认关闭。

---

# 第一部分：使用指南

## 1. 它做什么

- 每天按你设置的定时检查 MAA；
- 逐任务判断成功 / 失败 / 重试后成功；
- 失败时说明具体任务和原因，例如：
  - `刷理智（PR-A-1）：关卡识别失败`
  - `刷理智（1-7）：完成`
- 可选通过飞书 / Lark、通用 Webhook、Bark、钉钉、企业微信推送；
- 菜单栏显示当前状态、上次结果、定时；
- 可选自动启动 / 退出 MAA。

它不会操作游戏，不会修改 MAA 日志；只有你主动开启自动启停时，才会控制 MAA。

## 2. 安装

### 环境

- macOS 13+
- 已安装 MAA（默认 `/Applications/MAA.app`）
- 消息机器人 Webhook（可选，不填也能监控，只是收不到推送）
- Python 3.9+（macOS 自带 `/usr/bin/python3` 即可）

### 方式 A：发行包

**DMG（推荐）**：下载 `MaaNotify-1.1.0.dmg`，双击挂载后运行 `安装.command`。

**ZIP**：下载并解压 `MaaNotify-1.1.0.zip` 后：

```bash
cd MaaNotify-1.1.0
./install_menuapp.sh
```

发行包已经包含预构建 App，不需要 Xcode。

### 方式 B：源码

```bash
git clone https://github.com/BestJarvan/maa-notify.git
cd maa-notify
./install_menuapp.sh
```

源码安装需要 Xcode Command Line Tools：

```bash
xcode-select --install
```

安装完成后，菜单栏会出现 MAA 图标。

## 3. 首次配置

第一次使用：

### 第一步：选择 MAA 目录

系统会弹出目录选择框，并默认打开：

```text
~/Library/Containers/com.hguandl.MeoAsstMac/Data
```

这是 MAA 在 macOS 上的固定位置，直接点“使用此目录”即可。如果 MAA 数据在别处，可以点“选择…”重新授权；设置窗口里的路径也可以手动修改，修改后点“应用目录”。

App 只会读取这个目录。

### 第二步：填消息机器人（可选）

**不填机器人也能正常使用**：监控、菜单状态、历史记录都会工作，只是不会发送消息。需要推送时再配置：

1. 选机器人类型；
2. 填 Webhook；
3. 需要签名/关键词的机器人填对应字段；
4. 点 **发送测试消息**；
5. 点 **保存并应用**。

Webhook 默认固定显示 10 个圆点，不按真实长度显示；点右侧小眼睛或字段本身可查看/编辑完整内容。

### 第三步：确认定时

设置窗口会显示：

```text
监控定时：07:02 / 19:01
MAA 当前定时：07:02 / 19:01
```

“监控定时”是软件检测/启动 MAA 的时间数组，可以增删、手动输入。

## 4. 菜单栏

菜单只保留关键信息：

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

点 **设置…** 可以完成所有配置。

## 5. 设置窗口

| 区域 | 能做什么 |
|---|---|
| 消息机器人 | 选择类型、Webhook、secret、keyword、发送测试（机器人可选） |
| 定时与自动启停 | 增删监控定时、查看 MAA 当前定时、设置提前量和退出延迟 |
| MAA 目录 | 默认路径可手动修改、选择目录、应用目录、打开目录、手动启动/退出 MAA |
| 启动与外观 | 登录时自动启动、菜单栏图标 |
| 高级 | 打开 JSON 配置、查看状态 |

## 6. 消息机器人

机器人是可选的。不填写时，软件仍会监控并更新菜单/历史，只是不会发送通知。

| 类型 | 说明 |
|---|---|
| 飞书 / Lark | 交互卡片，支持 secret 和 keyword |
| 通用 Webhook | POST JSON，包含 `title`、`content`、`status`、`slot`、`report` |
| Bark | 使用 Bark 的 `/push` 接口 |
| 钉钉 | Markdown 消息，支持加签 |
| 企业微信 | Markdown 消息 |

通用 Webhook 示例：

```json
{
  "title": "⚠️ MAA 07:02 部分任务失败",
  "content": "任务：7/8 完成\n...",
  "status": "partial",
  "slot": "07:02",
  "date": "2026-11-26",
  "report": {}
}
```

高级配置支持：

- `notify.headers`：自定义请求头；
- `notify.body_template`：自定义 JSON 模板，占位符：
  - `{{title}}`
  - `{{content}}`
  - `{{status}}`
  - `{{slot}}`
  - `{{report_json}}`

## 7. 自动启停 MAA

在设置窗口开启：

- 定时前自动启动 MAA；
- 任务结束后自动退出 MAA；
- 提前几分钟启动；
- 报告发送后多少秒退出。

注意：

- Mac 在启动时间点睡眠时不会执行；
- MAA 更新通常 1 分钟左右，5 分钟提前量一般够用；
- 自动退出优先使用 AppleScript，失败时用 `SIGTERM` 兜底。

设置里的“登录时自动启动 MaaNotify”控制开机登录后是否自动运行本 App；关闭后仍可手动打开。

## 8. 常见问题

### 没有收到消息

- Webhook 是否填写，且不是 `REPLACE_ME`；
- 点“发送测试消息”看具体错误；
- 查看：
  ```bash
  tail -50 "$HOME/Library/Application Support/MaaNotify/menuapp/watcher.log"
  ```

### 菜单栏没有图标

```bash
launchctl print gui/$(id -u)/com.jarvan.maa-notify.menu
tail -30 "$HOME/Library/Application Support/MaaNotify/menuapp/launchd.err.log"
```

### 提示重新选择目录

系统让 security-scoped bookmark 失效了。重新选择：

```text
~/Library/Containers/com.hguandl.MeoAsstMac/Data
```

### 自动启停不生效

- 确认设置里两个开关都打开；
- 确认启动时间点 Mac 没有睡眠；
- 确认 MAA 路径是 `/Applications/MAA.app`。

## 9. 数据与隐私

- Webhook、secret 只保存在本机 `config.json`，权限 600；
- `config.json` 不在 Git 中；
- App 只获得你选择的 MAA `Data` 目录访问权；
- Python 只读镜像目录；
- 唯一外部网络请求是发送消息机器人 Webhook。

---

# 第二部分：开发与维护

## 1. 架构

```text
MAA.app
  ├─ gui.log
  └─ asst.log
        │
        ▼
MaaNotify.app（菜单栏）
  ├─ 目录授权 + 日志镜像
  ├─ 启动/守护 Python watcher
  └─ 设置窗口
        │
        ▼
maa_notify.py
  ├─ 解析日志
  ├─ 任务状态机
  └─ 消息机器人
```

App 通过系统文件选择框获得 MAA `Data` 目录访问权，把日志镜像到：

```text
~/Library/Application Support/MaaNotify/feed/
```

Python 只读这个镜像目录，因此不需要给 Python 开 FDA。

## 2. 代码结构

```text
maa-notify/
├── maa_notify.py             # 解析/状态机/通知/CLI
├── config.example.json
├── install_menuapp.sh        # 安装菜单栏版
├── uninstall_menuapp.sh
├── install.sh                # 旧命令行/LaunchAgent 方式
├── uninstall.sh
├── menuapp/
│   ├── MaaNotifyMenu.swift   # 主进程 / 菜单栏 / 镜像 / 守护
│   ├── SettingsView.swift    # 设置窗口（SwiftUI）
│   ├── Info.plist
│   ├── build.sh              # 编译 App
│   └── package.sh            # 生成发行 zip
├── tests/
│   └── test_maa_notify.py
├── LICENSE
└── README.md
```

运行数据：

```text
~/Applications/MaaNotify.app
~/Library/Application Support/MaaNotify/app/          # Python 运行时 + 基础配置
~/Library/Application Support/MaaNotify/menuapp/      # 书签/设置/状态/watcher 日志
~/Library/Application Support/MaaNotify/feed/         # 镜像日志
~/Library/LaunchAgents/com.jarvan.maa-notify.menu.plist
```

## 3. 本地构建

只编译 App：

```bash
cd menuapp
./build.sh
open build/MaaNotify.app
```

构建产物：

```text
menuapp/build/MaaNotify.app
```

安装到本机：

```bash
cd ..
./install_menuapp.sh
```

`install_menuapp.sh` 会：

1. 编译（如果源码里有 `menuapp/build.sh`）；
2. 复制 App 到 `~/Applications`；
3. 安装 Python 运行时和配置；
4. 写入并加载 LaunchAgent。

## 4. 打包发行版

```bash
./menuapp/package.sh
```

产物：

```text
dist/MaaNotify-1.1.0.zip
dist/MaaNotify-1.1.0.zip.sha256
dist/MaaNotify-1.1.0.dmg
dist/MaaNotify-1.1.0.dmg.sha256
```

两种包内容一致：

- 预构建 `MaaNotify.app`
- `maa_notify.py`
- `config.example.json`
- `install_menuapp.sh`（ZIP 用）
- `安装.command`（DMG 双击用）
- `README.md`
- `LICENSE`

解压/挂载后运行安装脚本即可，不需要 Xcode。

> `MaaNotify.app` 本身不是完全独立的：它引用安装到 Application Support 的 Python 运行时和配置。请使用安装脚本，不要只拷贝 `.app`。

## 5. 测试

```bash
cd maa-notify
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -v
```

当前 29 个测试，覆盖：

- 同名刷理智映射
- 重试/失败/取消/超时
- 资源更新不触发运行开始
- 自动启动时间窗
- 自动退出状态
- 多消息机器人 payload
- 定时数组
- 真实日志回放（本机不存在时跳过）

## 6. 配置

完整模板见 [config.example.json](config.example.json)。

旧版 `feishu` 配置仍兼容；新配置使用 `notify`。

核心字段：

```json
{
  "notify": {
    "provider": "feishu",
    "webhook": "",
    "secret": "",
    "keyword": "MAA"
  },
  "maa": {
    "schedule_times": ["07:02", "19:01"]
  },
  "policy": {
    "auto_start_maa": true,
    "auto_quit_maa": true,
    "auto_start_lead_minutes": 5,
    "auto_quit_delay_seconds": 60
  }
}
```

## 7. 扩展点

- 新消息机器人：在 `maa_notify.py` 的 `get_notify` / `build_feishu_payload` / `send_notification` 增加 provider；
- 新日志解析：改 `Run.handle_event` 和 `is_run_start_message`；
- 新任务类型：`TASK_LABEL_BY_KEY`、`TASK_KEY_BY_ALIAS`；
- 新菜单/设置项：`SettingsView.swift` + `MaaNotifyMenu.swift`；
- 新镜像文件：`MirrorEngine.specs`。

## 8. 发布流程

版本号在仓库根目录的 `VERSION` 文件中维护。当前：

```bash
cat VERSION
# 1.1.0
```

PR 合并到 `main` 后，GitHub Actions 会：

1. 重新运行测试；
2. 构建 zip 和 dmg；
3. 创建 `v$(cat VERSION)` 标签；
4. 创建 GitHub Release 并上传两个安装包。

如果该版本标签已存在，工作流会跳过发布，避免重复。

## 9. PR 自动审核

从其他分支向 `main` 提交 PR 时，会触发 `.github/workflows/pr-review.yml`：

1. 运行 29 个 Python 测试；
2. 编译 Swift 菜单栏 App；
3. 同时构建 zip 和 dmg；
4. 校验 dmg；
5. 通过后机器人自动审批，并打上 `auto-reviewed` 标签；
6. 失败则打上 `needs-fix` 标签并留言。

PR 合并到 `main` 后，`.github/workflows/release.yml` 会重新构建，并自动创建 GitHub Release，上传 zip、dmg 和校验文件。若 `v$(cat VERSION)` 已发布，则跳过，避免重复。

## TODO

- [ ] 任务前自动唤醒睡眠中的 Mac（`pmset` + 可选 root LaunchDaemon；需要管理员授权，暂不实现）
- [ ] 唤醒时间与 07:02 / 19:01 两个监控时段联动
- [ ] 唤醒助手安装/卸载与设置面板状态展示

## License

[MIT](LICENSE) © 2026 BestJarvan
