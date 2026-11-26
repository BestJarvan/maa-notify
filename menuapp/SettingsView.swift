import AppKit
import Combine
import SwiftUI

struct IconOption: Identifiable {
    let id: String
    let title: String
}

let kIconOptions: [IconOption] = [
    IconOption(id: "antenna", title: "天线（默认）"),
    IconOption(id: "gamecontroller", title: "手柄"),
    IconOption(id: "shield.lefthalf.filled", title: "盾牌"),
    IconOption(id: "bolt.horizontal.circle", title: "闪电"),
    IconOption(id: "waveform", title: "波形"),
    IconOption(id: "text", title: "MAA 文字"),
    IconOption(id: "maa", title: "MAA App 图标"),
]

final class SettingsModel: ObservableObject {
    @Published var provider: String
    @Published var webhook: String
    @Published var secret: String
    @Published var keyword: String
    @Published var live: Bool
    @Published var autoStart: Bool
    @Published var autoQuit: Bool
    @Published var leadMinutes: Int
    @Published var quitDelay: Int
    @Published var icon: String
    @Published var scheduleTimes: [String]
    @Published var maaSchedule: String
    @Published var folderPath: String
    @Published var status: String
    @Published var testing: Bool

    init(
        provider: String,
        webhook: String,
        secret: String,
        keyword: String,
        live: Bool,
        autoStart: Bool,
        autoQuit: Bool,
        leadMinutes: Int,
        quitDelay: Int,
        icon: String,
        scheduleTimes: [String],
        maaSchedule: String,
        folderPath: String,
        status: String
    ) {
        self.provider = provider
        self.webhook = webhook
        self.secret = secret
        self.keyword = keyword
        self.live = live
        self.autoStart = autoStart
        self.autoQuit = autoQuit
        self.leadMinutes = leadMinutes
        self.quitDelay = quitDelay
        self.icon = icon
        self.scheduleTimes = scheduleTimes
        self.maaSchedule = maaSchedule
        self.folderPath = folderPath
        self.status = status
        self.testing = false
    }

    var isWebhookConfigured: Bool {
        let value = webhook.trimmingCharacters(in: .whitespacesAndNewlines)
        return !value.isEmpty && !value.contains("REPLACE_ME")
    }

    var needsSetup: Bool {
        !isWebhookConfigured || folderPath.isEmpty
    }
}

struct SettingsActions {
    let save: () -> Void
    let test: () -> Void
    let chooseFolder: () -> Void
    let readMAA: () -> Void
    let syncMAA: () -> Void
    let refreshSchedule: () -> Void
    let startMAA: () -> Void
    let quitMAA: () -> Void
    let openAdvanced: () -> Void
    let close: () -> Void
}

struct SettingsView: View {
    @ObservedObject var model: SettingsModel
    let actions: SettingsActions

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if model.needsSetup {
                        onboarding
                    }
                    feishuCard
                    scheduleCard
                    maaCard
                    appearanceCard
                    HStack {
                        Text(model.status)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                        Spacer()
                        Button("高级配置（JSON）") { actions.openAdvanced() }
                            .buttonStyle(.link)
                    }
                }
                .padding(18)
            }
            Divider()
            footer
        }
        .frame(width: 600, height: 720)
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "antenna.radiowaves.left.and.right")
                .font(.title2)
                .foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 3) {
                Text("MAA 状态监控").font(.title2.bold())
                Text("配置保存在本机，Webhook 不会上传。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(18)
    }

    private var onboarding: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("三步开始监控").font(.headline)
            step(1, "选择 MAA 的 Data 目录", done: !model.folderPath.isEmpty)
            step(2, "粘贴消息机器人 Webhook", done: model.isWebhookConfigured)
            step(3, "发送测试消息", done: false)
            Text("支持飞书 / 通用 Webhook / Bark / 钉钉 / 企业微信。不会给 Python 开“完全磁盘访问权限”。")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.accentColor.opacity(0.08))
        .cornerRadius(10)
    }

    private func step(_ number: Int, _ text: String, done: Bool) -> some View {
        HStack(spacing: 8) {
            Image(systemName: done ? "checkmark.circle.fill" : "\(number).circle")
                .foregroundStyle(done ? .green : .secondary)
            Text(text).font(.callout)
        }
    }

    private var feishuCard: some View {
        card("消息机器人") {
            Picker("机器人类型", selection: $model.provider) {
                Text("飞书 / Lark").tag("feishu")
                Text("通用 Webhook").tag("generic")
                Text("Bark").tag("bark")
                Text("钉钉").tag("dingtalk")
                Text("企业微信").tag("wecom")
            }
            .pickerStyle(.menu)
            TextField("Webhook 地址", text: $model.webhook)
                .textFieldStyle(.roundedBorder)
            SecureField("签名密钥（飞书/钉钉，可选）", text: $model.secret)
                .textFieldStyle(.roundedBorder)
            TextField("自定义关键词（可选）", text: $model.keyword)
                .textFieldStyle(.roundedBorder)
            Toggle("启用消息推送（关闭时只记录日志）", isOn: $model.live)
            HStack {
                Button(model.testing ? "发送中…" : "发送测试消息") { actions.test() }
                    .disabled(model.testing || !model.isWebhookConfigured)
                if model.testing {
                    ProgressView().controlSize(.small)
                }
                Spacer()
            }
        }
    }

    private var scheduleCard: some View {
        card("定时与自动启停") {
            Text("监控定时").font(.subheadline).fontWeight(.medium)
            ForEach(Array(model.scheduleTimes.enumerated()), id: \.offset) { index, _ in
                HStack(spacing: 8) {
                    TextField("HH:mm", text: Binding(
                        get: { model.scheduleTimes.indices.contains(index) ? model.scheduleTimes[index] : "" },
                        set: { value in
                            if model.scheduleTimes.indices.contains(index) {
                                model.scheduleTimes[index] = value
                            }
                        }
                    ))
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 90)
                    Button {
                        if model.scheduleTimes.indices.contains(index) {
                            model.scheduleTimes.remove(at: index)
                        }
                    } label: {
                        Image(systemName: "minus.circle")
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.red)
                    Spacer()
                }
            }
            HStack {
                Button("添加时间") {
                    model.scheduleTimes.append("12:00")
                }
                Button("从 MAA 读取") { actions.readMAA() }
                Spacer()
                Button("写入 MAA 定时…") { actions.syncMAA() }
                    .disabled(model.scheduleTimes.isEmpty)
            }

            HStack {
                Text("MAA 当前定时")
                Spacer()
                Text(model.maaSchedule.isEmpty ? "未读取" : model.maaSchedule)
                    .foregroundStyle(.secondary)
                Button("刷新") { actions.refreshSchedule() }
                    .buttonStyle(.link)
            }

            Toggle("定时前自动启动 MAA", isOn: $model.autoStart)
            Toggle("任务结束后自动退出 MAA", isOn: $model.autoQuit)
            Stepper(value: $model.leadMinutes, in: 1...30) {
                Text("提前 \(model.leadMinutes) 分钟启动")
            }
            Stepper(value: $model.quitDelay, in: 0...600, step: 10) {
                Text("报告发送后 \(model.quitDelay) 秒退出")
            }
            Text("“监控定时”决定软件何时检测/启动 MAA；改动后如需 MAA 自己也在新时间执行，请点“写入 MAA 定时…”。")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var maaCard: some View {
        card("MAA 目录") {
            Text(model.folderPath.isEmpty ? "尚未选择" : model.folderPath)
                .font(.caption)
                .textSelection(.enabled)
                .foregroundStyle(.secondary)
                .lineLimit(2)
            HStack {
                Button("重新选择…") { actions.chooseFolder() }
                Button("打开目录") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: model.folderPath))
                }
                .disabled(model.folderPath.isEmpty)
                Spacer()
                Button("启动 MAA") { actions.startMAA() }
                Button("退出 MAA") { actions.quitMAA() }
            }
        }
    }

    private var appearanceCard: some View {
        card("外观") {
            Picker("菜单栏图标", selection: $model.icon) {
                ForEach(kIconOptions) { option in
                    Text(option.title).tag(option.id)
                }
            }
            .pickerStyle(.menu)
        }
    }

    private var footer: some View {
        HStack {
            Text("修改后点“保存并应用”立即生效。")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            Button("关闭") { actions.close() }
                .keyboardShortcut(.cancelAction)
            Button("保存并应用") { actions.save() }
                .keyboardShortcut(.defaultAction)
        }
        .padding(14)
    }

    private func card<Content: View>(
        _ title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            content()
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor))
        .cornerRadius(10)
    }
}

final class SettingsWindowController: NSWindowController {
    init(model: SettingsModel, actions: SettingsActions) {
        let hosting = NSHostingController(
            rootView: SettingsView(model: model, actions: actions)
        )
        let window = NSWindow(contentViewController: hosting)
        window.title = "MAA 状态监控设置"
        window.styleMask = [.titled, .closable, .miniaturizable]
        window.setContentSize(NSSize(width: 600, height: 720))
        window.minSize = NSSize(width: 560, height: 600)
        window.center()
        window.isReleasedWhenClosed = false
        super.init(window: window)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}
