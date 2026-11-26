import Cocoa
import Darwin
import Foundation

// MARK: - Paths

let kHome = FileManager.default.homeDirectoryForCurrentUser
let kAppSupport = kHome.appendingPathComponent("Library/Application Support/MaaNotify", isDirectory: true)
let kMenuDir = kAppSupport.appendingPathComponent("menuapp", isDirectory: true)
let kFeedDir = kAppSupport.appendingPathComponent("feed", isDirectory: true)
let kStateDir = kMenuDir.appendingPathComponent("state", isDirectory: true)
let kMenuConfig = kMenuDir.appendingPathComponent("config.json")
let kBookmark = kMenuDir.appendingPathComponent("bookmark.data")
let kBookmarkPath = kMenuDir.appendingPathComponent("bookmark.path")
let kStatus = kMenuDir.appendingPathComponent("status.json")
let kMirrorState = kMenuDir.appendingPathComponent("mirror-state.json")
let kSettings = kMenuDir.appendingPathComponent("settings.json")
let kWatcherLog = kMenuDir.appendingPathComponent("watcher.log")
let kWatcherPid = kMenuDir.appendingPathComponent("watcher.pid")
let kRuntime = kAppSupport.appendingPathComponent("app/maa_notify.py")
let kBaseConfig = kAppSupport.appendingPathComponent("app/config.json")
let kPython = "/usr/bin/python3"
let kMaaAppPath = "/Applications/MAA.app"

let kGuiRelative = "Library/Application Support/debug/gui.log"
let kAsstRelative = "Library/Application Support/debug/asst.log"
let kAsstBakRelative = "Library/Application Support/debug/asst.bak.log"
let kTasksRelative = "Library/Application Support/DailyTasks/Default.plist"
let kPrefsRelative = "Library/Preferences/com.hguandl.MeoAsstMac.plist"

// MARK: - Helpers

func ensureDirectory(_ url: URL) {
    try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
}

func loadJSON(_ url: URL) -> [String: Any]? {
    guard let data = try? Data(contentsOf: url) else { return nil }
    guard let obj = try? JSONSerialization.jsonObject(with: data) else { return nil }
    return obj as? [String: Any]
}

func writeJSON(_ object: [String: Any], to url: URL) {
    guard let data = try? JSONSerialization.data(
        withJSONObject: object,
        options: [.prettyPrinted, .sortedKeys]
    ) else { return }
    ensureDirectory(url.deletingLastPathComponent())
    try? data.write(to: url, options: .atomic)
    try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
}

func canReadOneByte(_ url: URL) -> Bool {
    guard let handle = try? FileHandle(forReadingFrom: url) else { return false }
    defer { try? handle.close() }
    return (try? handle.read(upToCount: 1)) != nil
}

func formatAge(_ date: Date?) -> String {
    guard let date else { return "未知" }
    let seconds = max(0, Int(Date().timeIntervalSince(date)))
    if seconds < 60 { return "\(seconds) 秒前" }
    if seconds < 3600 { return "\(seconds / 60) 分钟前" }
    return "\(seconds / 3600) 小时前"
}

// MARK: - Mirror

struct SourceSpec {
    let relative: String
    let dest: String
    let tailBytes: Int64
    let wholeCopy: Bool
    let required: Bool
}

struct FileState {
    var inode: UInt64 = 0
    var offset: Int64 = 0
    var size: Int64 = 0
    var mtime: TimeInterval = 0
}

final class MirrorEngine {
    let sourceRoot: URL
    let feedRoot: URL
    let statusURL: URL
    let stateURL: URL
    private let queue = DispatchQueue(label: "com.jarvan.maa-notify.mirror")
    private var timer: Timer?
    private var syncing = false
    private var states: [String: FileState] = [:]
    private(set) var lastError: String?

    private let specs: [SourceSpec] = [
        SourceSpec(relative: kGuiRelative, dest: "gui.log", tailBytes: 0, wholeCopy: false, required: true),
        SourceSpec(relative: kAsstRelative, dest: "asst.log", tailBytes: 0, wholeCopy: false, required: false),
        SourceSpec(relative: kAsstBakRelative, dest: "asst.bak.log", tailBytes: 8 * 1024 * 1024, wholeCopy: false, required: false),
        SourceSpec(relative: kTasksRelative, dest: "Default.plist", tailBytes: 0, wholeCopy: true, required: true),
        SourceSpec(relative: kPrefsRelative, dest: "prefs.plist", tailBytes: 0, wholeCopy: true, required: false),
    ]

    init(sourceRoot: URL, feedRoot: URL, statusURL: URL, stateURL: URL) {
        self.sourceRoot = sourceRoot
        self.feedRoot = feedRoot
        self.statusURL = statusURL
        self.stateURL = stateURL
        if let obj = loadJSON(stateURL),
           let files = obj["files"] as? [String: Any] {
            for (name, raw) in files {
                guard let item = raw as? [String: Any] else { continue }
                var state = FileState()
                state.inode = (item["inode"] as? NSNumber)?.uint64Value ?? 0
                state.offset = (item["offset"] as? NSNumber)?.int64Value ?? 0
                state.size = (item["size"] as? NSNumber)?.int64Value ?? 0
                state.mtime = (item["mtime"] as? NSNumber)?.doubleValue ?? 0
                states[name] = state
            }
        }
    }

    func start(completion: @escaping (String?) -> Void) {
        ensureDirectory(feedRoot)
        queue.async {
            let error = self.syncAll()
            DispatchQueue.main.async {
                self.timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
                    self?.timerFired()
                }
                if let timer = self.timer {
                    RunLoop.main.add(timer, forMode: .common)
                }
                completion(error)
            }
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func timerFired() {
        if syncing { return }
        syncing = true
        queue.async {
            _ = self.syncAll()
            DispatchQueue.main.async {
                self.syncing = false
            }
        }
    }

    private func syncAll() -> String? {
        ensureDirectory(feedRoot)
        var firstError: String?
        var fileStatus: [String: Any] = [:]

        for spec in specs {
            do {
                let info = try sync(spec)
                fileStatus[spec.dest] = info
            } catch {
                let message = "\(spec.dest): \(error.localizedDescription)"
                if firstError == nil { firstError = message }
                fileStatus[spec.dest] = ["error": message]
            }
        }

        self.lastError = firstError
        var stateObject: [String: Any] = [:]
        for (name, item) in states {
            stateObject[name] = [
                "inode": item.inode,
                "offset": item.offset,
                "size": item.size,
                "mtime": item.mtime,
            ]
        }
        writeJSON(["files": stateObject], to: stateURL)

        let status: [String: Any] = [
            "source": sourceRoot.path,
            "lastSync": ISO8601DateFormatter().string(from: Date()),
            "error": firstError ?? "",
            "files": fileStatus,
        ]
        writeJSON(status, to: statusURL)
        return firstError
    }

    private func sync(_ spec: SourceSpec) throws -> [String: Any] {
        let source = sourceRoot.appendingPathComponent(spec.relative)
        let dest = feedRoot.appendingPathComponent(spec.dest)
        let fm = FileManager.default

        guard fm.fileExists(atPath: source.path) else {
            if spec.required {
                throw NSError(domain: "MaaNotify", code: 1, userInfo: [
                    NSLocalizedDescriptionKey: "源文件不存在"
                ])
            }
            return ["missing": true]
        }

        let attrs = try fm.attributesOfItem(atPath: source.path)
        let size = (attrs[.size] as? NSNumber)?.int64Value ?? 0
        let inode = (attrs[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0
        let mtime = (attrs[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0

        var state = states[spec.dest] ?? FileState()

        if spec.wholeCopy {
            if !fm.fileExists(atPath: dest.path) || state.mtime != mtime || state.size != size {
                let tmp = dest.appendingPathExtension("tmp")
                try? fm.removeItem(at: tmp)
                try fm.copyItem(at: source, to: tmp)
                try? fm.removeItem(at: dest)
                try fm.moveItem(at: tmp, to: dest)
                state.size = size
                state.mtime = mtime
                state.inode = inode
                states[spec.dest] = state
            }
            let destSize = (try? fm.attributesOfItem(atPath: dest.path)[.size] as? NSNumber)?.int64Value ?? 0
            return [
                "sourceSize": size,
                "destSize": destSize,
                "offset": 0,
                "inode": inode,
            ]
        }

        if !fm.fileExists(atPath: dest.path) {
            fm.createFile(atPath: dest.path, contents: nil)
            state.offset = spec.tailBytes > 0 ? max(0, size - spec.tailBytes) : 0
            state.inode = inode
        } else if state.inode == 0 {
            let destSize = (try? fm.attributesOfItem(atPath: dest.path)[.size] as? NSNumber)?.int64Value ?? 0
            if spec.tailBytes > 0 || destSize > size {
                // 没有持久化 offset 且 feed 不是从头开始：清空后重新镜像。
                try? fm.removeItem(at: dest)
                fm.createFile(atPath: dest.path, contents: nil)
                state.offset = spec.tailBytes > 0 ? max(0, size - spec.tailBytes) : 0
            } else {
                // feed 已经包含 source 的前 destSize 字节，从这里继续。
                state.offset = destSize
            }
            state.inode = inode
        }

        if state.inode != inode {
            state.offset = spec.tailBytes > 0 ? max(0, size - spec.tailBytes) : 0
            state.inode = inode
        }
        if size < state.offset {
            state.offset = 0
        }

        if size > state.offset {
            let srcHandle = try FileHandle(forReadingFrom: source)
            defer { try? srcHandle.close() }
            try srcHandle.seek(toOffset: UInt64(state.offset))

            let dstHandle = try FileHandle(forWritingTo: dest)
            defer { try? dstHandle.close() }
            try dstHandle.seekToEnd()

            var remaining = size - state.offset
            while remaining > 0 {
                let want = min(remaining, 1_048_576)
                let data = try srcHandle.read(upToCount: Int(want)) ?? Data()
                if data.isEmpty { break }
                try dstHandle.write(contentsOf: data)
                state.offset += Int64(data.count)
                remaining -= Int64(data.count)
            }
        }

        state.size = size
        state.mtime = mtime
        states[spec.dest] = state

        let destSize = (try? fm.attributesOfItem(atPath: dest.path)[.size] as? NSNumber)?.int64Value ?? 0
        return [
            "sourceSize": size,
            "destSize": destSize,
            "offset": state.offset,
            "inode": inode,
        ]
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var mirror: MirrorEngine?
    private var watcher: Process?
    private var watcherLogHandle: FileHandle?
    private var sourceURL: URL?
    private var securityScoped = false
    private var restartWork: DispatchWorkItem?
    private var quitting = false
    private var lastWatcherExit: Int32?
    private var iconName = "antenna"
    private var settingsWindow: SettingsWindowController?
    private var settingsModel: SettingsModel?
    private var testProcess: Process?
    private var syncProcess: Process?

    func applicationDidFinishLaunching(_ notification: Notification) {
        ensureDirectory(kAppSupport)
        ensureDirectory(kMenuDir)
        ensureDirectory(kStateDir)
        ensureDirectory(kFeedDir)

        NSApp.setActivationPolicy(.accessory)
        if let settings = loadJSON(kSettings), let name = settings["icon"] as? String {
            iconName = name
        }
        setupStatusItem()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            self.bootstrap()
        }
        if CommandLine.arguments.contains("--settings") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
                self.openSettings()
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        restartWork?.cancel()
        mirror?.stop()
        mirror = nil
        stopWatcher()
        if securityScoped {
            sourceURL?.stopAccessingSecurityScopedResource()
            securityScoped = false
        }
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.toolTip = "MAA 状态监控"
        }
        applyIcon()
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
    }

    // MARK: startup / authorization

    private func bootstrap() {
        if let env = ProcessInfo.processInfo.environment["MAA_NOTIFY_SOURCE_DIR"], !env.isEmpty {
            let url = URL(fileURLWithPath: env)
            if validateSource(url) {
                sourceURL = url
                startMonitoring()
                maybeShowOnboarding()
                return
            }
        }

        if let data = try? Data(contentsOf: kBookmark), let url = resolveBookmark(data), validateSource(url) {
            sourceURL = url
            startMonitoring()
            maybeShowOnboarding()
            return
        }

        if let path = try? String(contentsOf: kBookmarkPath, encoding: .utf8) {
            let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                let url = URL(fileURLWithPath: trimmed)
                if validateSource(url) {
                    sourceURL = url
                    startMonitoring()
                    maybeShowOnboarding()
                    return
                }
            }
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            self.promptForFolder()
        }
    }

    private func resolveBookmark(_ data: Data) -> URL? {
        var stale = false
        if let url = try? URL(
            resolvingBookmarkData: data,
            options: [.withSecurityScope],
            relativeTo: nil,
            bookmarkDataIsStale: &stale
        ) {
            if url.startAccessingSecurityScopedResource() {
                securityScoped = true
            }
            return url
        }
        if let url = try? URL(
            resolvingBookmarkData: data,
            options: [],
            relativeTo: nil,
            bookmarkDataIsStale: &stale
        ) {
            return url
        }
        return nil
    }

    private func normalizeSource(_ url: URL) -> URL? {
        let gui = url.appendingPathComponent(kGuiRelative)
        if FileManager.default.fileExists(atPath: gui.path) {
            return url
        }
        if url.lastPathComponent == "debug" {
            let data = url
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
            if FileManager.default.fileExists(atPath: data.appendingPathComponent(kGuiRelative).path) {
                return data
            }
        }
        return nil
    }

    private func validateSource(_ url: URL) -> Bool {
        guard let dataURL = normalizeSource(url) else { return false }
        let gui = dataURL.appendingPathComponent(kGuiRelative)
        let tasks = dataURL.appendingPathComponent(kTasksRelative)
        return canReadOneByte(gui) && canReadOneByte(tasks)
    }

    private func promptForFolder(after: (() -> Void)? = nil) {
        NSApp.activate(ignoringOtherApps: true)
        let panel = NSOpenPanel()
        panel.title = "选择 MAA Data 目录"
        panel.message = "请选择 MAA 的 Data 目录。App 只会读取这个目录，不需要“完全磁盘访问权限”。"
        panel.prompt = "使用此目录"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = kHome.appendingPathComponent(
            "Library/Containers/com.hguandl.MeoAsstMac/Data",
            isDirectory: true
        )

        if panel.runModal() == .OK, let selected = panel.url {
            guard let dataURL = normalizeSource(selected), validateSource(dataURL) else {
                let alert = NSAlert()
                alert.messageText = "目录不正确"
                alert.informativeText = "请选择 MAA 的 Data 目录，确保其中包含 Library/Application Support/debug/gui.log 和 DailyTasks/Default.plist。"
                alert.alertStyle = .warning
                alert.runModal()
                return
            }
            sourceURL = dataURL
            saveBookmark(dataURL)
            startMonitoring()
            after?()
            maybeShowOnboarding()
        }
    }

    private func saveBookmark(_ url: URL) {
        if securityScoped {
            url.stopAccessingSecurityScopedResource()
            securityScoped = false
        }
        if url.startAccessingSecurityScopedResource() {
            securityScoped = true
        }

        var data: Data?
        let options: URL.BookmarkCreationOptions = [.withSecurityScope, .securityScopeAllowOnlyReadAccess]
        data = try? url.bookmarkData(
            options: options,
            includingResourceValuesForKeys: nil,
            relativeTo: nil
        )
        if data == nil {
            data = try? url.bookmarkData(
                options: [],
                includingResourceValuesForKeys: nil,
                relativeTo: nil
            )
        }
        if let data {
            try? data.write(to: kBookmark, options: .atomic)
        }
        try? url.path.write(to: kBookmarkPath, atomically: true, encoding: .utf8)
    }

    // MARK: monitoring

    private func startMonitoring() {
        guard let sourceURL else { return }
        stopWatcher()
        mirror?.stop()
        ensureMenuConfig()

        let engine = MirrorEngine(
            sourceRoot: sourceURL,
            feedRoot: kFeedDir,
            statusURL: kStatus,
            stateURL: kMirrorState
        )
        mirror = engine
        engine.start { [weak self] error in
            guard let self else { return }
            if let error {
                self.lastWatcherExit = nil
                NSLog("mirror initial error: %@", error)
            }
            self.startWatcher()
        }
    }

    private func stopMonitoring() {
        mirror?.stop()
        mirror = nil
        stopWatcher()
    }

    private func ensureMenuConfig() {
        let base = loadJSON(kBaseConfig) ?? [:]
        var existing = loadJSON(kMenuConfig) ?? [:]

        var maa = base["maa"] as? [String: Any] ?? [:]
        maa["gui_log"] = kFeedDir.appendingPathComponent("gui.log").path
        maa["asst_log"] = kFeedDir.appendingPathComponent("asst.log").path
        maa["asst_bak_log"] = kFeedDir.appendingPathComponent("asst.bak.log").path
        maa["interface_dir"] = kFeedDir.appendingPathComponent("interface").path
        maa["daily_tasks_plist"] = kFeedDir.appendingPathComponent("Default.plist").path
        maa["schedule_plist"] = kFeedDir.appendingPathComponent("prefs.plist").path
        maa["schedule_domain"] = "com.hguandl.MeoAsstMac"
        maa["schedule_key"] = "MAAScheduledDailyTaskTimer"
        maa["app_path"] = kMaaAppPath
        if maa["fallback_times"] == nil {
            maa["fallback_times"] = ["07:02", "19:01"]
        }
        existing["maa"] = maa

        if let notify = base["notify"] {
            existing["notify"] = notify
        }
        if let feishu = base["feishu"] {
            existing["feishu"] = feishu
        }
        var policy = base["policy"] as? [String: Any] ?? [:]
        if let oldPolicy = existing["policy"] as? [String: Any] {
            for key in [
                "auto_start_maa",
                "auto_quit_maa",
                "auto_start_lead_minutes",
                "auto_quit_delay_seconds",
            ] {
                if policy[key] == nil, let value = oldPolicy[key] {
                    policy[key] = value
                }
            }
        }
        existing["policy"] = policy

        var runtime = existing["runtime"] as? [String: Any] ?? [:]
        if runtime["dry_run"] == nil {
            runtime["dry_run"] = true
        }
        existing["runtime"] = runtime

        ensureDirectory(kFeedDir.appendingPathComponent("interface", isDirectory: true))
        writeJSON(existing, to: kMenuConfig)
    }

    private func startWatcher() {
        guard sourceURL != nil else { return }
        ensureMenuConfig()
        killStaleWatcher()

        ensureDirectory(kMenuDir)
        FileManager.default.createFile(atPath: kWatcherLog.path, contents: nil)
        guard let handle = try? FileHandle(forWritingTo: kWatcherLog) else { return }
        _ = try? handle.seekToEnd()
        watcherLogHandle = handle

        let process = Process()
        process.executableURL = URL(fileURLWithPath: kPython)
        process.arguments = [
            "-B", kRuntime.path,
            "--config", kMenuConfig.path,
            "--state-dir", kStateDir.path,
            "watch",
        ]
        var env = ProcessInfo.processInfo.environment
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        process.environment = env
        process.standardOutput = handle
        process.standardError = handle

        process.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self else { return }
                self.lastWatcherExit = proc.terminationStatus
                self.watcher = nil
                try? self.watcherLogHandle?.close()
                self.watcherLogHandle = nil
                self.removePid()
                if !self.quitting {
                    self.scheduleWatcherRestart()
                }
            }
        }

        do {
            try process.run()
            watcher = process
            try? "\(process.processIdentifier)".write(
                to: kWatcherPid,
                atomically: true,
                encoding: .utf8
            )
        } catch {
            NSLog("start watcher failed: %@", error.localizedDescription)
            scheduleWatcherRestart()
        }
    }

    private func scheduleWatcherRestart() {
        restartWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self, !self.quitting else { return }
            self.startWatcher()
        }
        restartWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 5, execute: work)
    }

    private func stopWatcher() {
        restartWork?.cancel()
        restartWork = nil
        if let process = watcher {
            process.terminationHandler = nil
            process.terminate()
            let deadline = Date().addingTimeInterval(2)
            while process.isRunning && Date() < deadline {
                usleep(100_000)
            }
            if process.isRunning {
                kill(process.processIdentifier, SIGKILL)
            }
            process.waitUntilExit()
            watcher = nil
        }
        try? watcherLogHandle?.close()
        watcherLogHandle = nil
        removePid()

        // 兜底：清掉极端情况下残留的菜单栏版 watcher。
        let pkill = Process()
        pkill.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        pkill.arguments = ["-f", kMenuConfig.path]
        try? pkill.run()
        pkill.waitUntilExit()
    }

    private func killStaleWatcher() {
        // 用 pkill 按 config 路径精确清理上一次 app 留下的 watcher。
        // 手动 watcher 用的是 app/config.json，不会被匹配。
        let pkill = Process()
        pkill.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        pkill.arguments = ["-f", kMenuConfig.path]
        try? pkill.run()
        pkill.waitUntilExit()
        usleep(200_000)
        removePid()
    }

    private func removePid() {
        try? FileManager.default.removeItem(at: kWatcherPid)
    }

    // MARK: settings

    private func webhookConfigured() -> Bool {
        let base = loadJSON(kBaseConfig) ?? [:]
        let notify = base["notify"] as? [String: Any] ?? [:]
        let feishu = base["feishu"] as? [String: Any] ?? [:]
        let value = ((notify["webhook"] as? String) ?? (feishu["webhook"] as? String) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return !value.isEmpty && !value.contains("REPLACE_ME")
    }

    private func maybeShowOnboarding() {
        guard !webhookConfigured() else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            self.openSettings()
        }
    }

    private func makeSettingsModel() -> SettingsModel {
        let base = loadJSON(kBaseConfig) ?? [:]
        let notify = base["notify"] as? [String: Any] ?? [:]
        let feishu = base["feishu"] as? [String: Any] ?? [:]
        let policy = base["policy"] as? [String: Any] ?? [:]
        let menu = loadJSON(kMenuConfig) ?? [:]
        let runtime = menu["runtime"] as? [String: Any] ?? [:]
        let settings = loadJSON(kSettings) ?? [:]
        let folder = sourceURL?.path
            ?? (try? String(contentsOf: kBookmarkPath, encoding: .utf8))?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            ?? ""

        let provider = (notify["provider"] as? String) ?? "feishu"
        let webhook = (notify["webhook"] as? String)
            ?? (feishu["webhook"] as? String)
            ?? ""
        let secret = (notify["secret"] as? String)
            ?? (feishu["secret"] as? String)
            ?? ""
        let keyword = (notify["keyword"] as? String)
            ?? (feishu["keyword"] as? String)
            ?? "MAA"

        return SettingsModel(
            provider: provider,
            webhook: webhook,
            secret: secret,
            keyword: keyword,
            live: !((runtime["dry_run"] as? Bool) ?? true),
            autoStart: policy["auto_start_maa"] as? Bool ?? false,
            autoQuit: policy["auto_quit_maa"] as? Bool ?? false,
            leadMinutes: (policy["auto_start_lead_minutes"] as? NSNumber)?.intValue ?? 5,
            quitDelay: (policy["auto_quit_delay_seconds"] as? NSNumber)?.intValue ?? 60,
            icon: settings["icon"] as? String ?? iconName,
            scheduleTimes: configuredScheduleList(),
            maaSchedule: readMaaScheduleText(),
            folderPath: folder,
            status: sourceURL == nil
                ? "还没有选择 MAA 目录。"
                : "配置保存在本机，Webhook 不会上传。"
        )
    }

    private func refreshSettingsModel() {
        guard let model = settingsModel else { return }
        let fresh = makeSettingsModel()
        model.provider = fresh.provider
        model.webhook = fresh.webhook
        model.secret = fresh.secret
        model.keyword = fresh.keyword
        model.live = fresh.live
        model.autoStart = fresh.autoStart
        model.autoQuit = fresh.autoQuit
        model.leadMinutes = fresh.leadMinutes
        model.quitDelay = fresh.quitDelay
        model.icon = fresh.icon
        model.scheduleTimes = fresh.scheduleTimes
        model.maaSchedule = fresh.maaSchedule
        model.folderPath = fresh.folderPath
        model.status = fresh.status
    }

    func openSettings() {
        if settingsModel == nil {
            settingsModel = makeSettingsModel()
        } else {
            refreshSettingsModel()
        }
        guard let model = settingsModel else { return }

        if settingsWindow == nil {
            let actions = SettingsActions(
                save: { [weak self] in self?.saveSettings() },
                test: { [weak self] in self?.testWebhook() },
                chooseFolder: { [weak self] in
                    self?.promptForFolder {
                        self?.refreshSettingsModel()
                        self?.settingsModel?.status = "MAA 目录已更新。"
                    }
                },
                readMAA: { [weak self] in
                    self?.settingsModel?.scheduleTimes = self?.readMaaScheduleList() ?? []
                    self?.settingsModel?.status = "已从 MAA 读取定时。"
                },
                syncMAA: { [weak self] in self?.syncMAASchedule() },
                refreshSchedule: { [weak self] in
                    self?.settingsModel?.maaSchedule = self?.readMaaScheduleText() ?? ""
                    self?.settingsModel?.status = "已刷新 MAA 定时。"
                },
                startMAA: { [weak self] in self?.startMAA() },
                quitMAA: { [weak self] in self?.quitMAA() },
                openAdvanced: {
                    NSWorkspace.shared.open(kBaseConfig)
                },
                close: { [weak self] in
                    self?.settingsWindow?.window?.orderOut(nil)
                }
            )
            settingsWindow = SettingsWindowController(model: model, actions: actions)
        }

        settingsWindow?.showWindow(nil)
        settingsWindow?.window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func saveSettings() {
        guard let model = settingsModel else { return }
        let webhook = model.webhook.trimmingCharacters(in: .whitespacesAndNewlines)
        let secret = model.secret.trimmingCharacters(in: .whitespacesAndNewlines)
        let keyword = model.keyword.trimmingCharacters(in: .whitespacesAndNewlines)
        let canLive = !webhook.isEmpty && !webhook.contains("REPLACE_ME")

        var base = loadJSON(kBaseConfig) ?? [:]
        var notify = base["notify"] as? [String: Any] ?? [:]
        notify["provider"] = model.provider
        notify["webhook"] = webhook
        notify["secret"] = secret
        notify["keyword"] = keyword
        if notify["notify_on_success"] == nil { notify["notify_on_success"] = true }
        if notify["notify_on_partial"] == nil { notify["notify_on_partial"] = true }
        if notify["notify_on_missed"] == nil { notify["notify_on_missed"] = true }
        base["notify"] = notify
        
        var feishu = base["feishu"] as? [String: Any] ?? [:]
        feishu["webhook"] = webhook
        feishu["secret"] = secret
        feishu["keyword"] = keyword
        base["feishu"] = feishu

        var maa = base["maa"] as? [String: Any] ?? [:]
        let times = model.scheduleTimes
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        if times.isEmpty {
            maa.removeValue(forKey: "schedule_times")
        } else {
            maa["schedule_times"] = times
        }
        base["maa"] = maa

        var policy = base["policy"] as? [String: Any] ?? [:]
        policy["auto_start_maa"] = model.autoStart
        policy["auto_quit_maa"] = model.autoQuit
        policy["auto_start_lead_minutes"] = max(1, model.leadMinutes)
        policy["auto_quit_delay_seconds"] = max(0, model.quitDelay)
        base["policy"] = policy
        writeJSON(base, to: kBaseConfig)

        ensureMenuConfig()
        var menu = loadJSON(kMenuConfig) ?? [:]
        var runtime = menu["runtime"] as? [String: Any] ?? [:]
        runtime["dry_run"] = !(model.live && canLive)
        menu["runtime"] = runtime
        writeJSON(menu, to: kMenuConfig)

        iconName = model.icon
        saveIconSetting()
        applyIcon()

        stopWatcher()
        startWatcher()

        if !canLive {
            model.live = false
            model.status = "已保存；Webhook 未填写，飞书推送保持关闭。"
        } else {
            model.status = "已保存并应用。"
        }
        refreshSettingsModel()
        model.status = canLive ? "已保存并应用。" : "已保存；Webhook 未填写，飞书推送保持关闭。"
    }

    private func testWebhook() {
        guard let model = settingsModel else { return }
        guard model.isWebhookConfigured else {
            model.status = "请先填写飞书 Webhook。"
            return
        }
        saveSettings()
        model.testing = true
        model.status = "正在发送测试消息…"

        let process = Process()
        process.executableURL = URL(fileURLWithPath: kPython)
        process.arguments = ["-B", kRuntime.path, "--config", kBaseConfig.path, "test"]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        process.terminationHandler = { [weak self] proc in
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let output = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            DispatchQueue.main.async {
                self?.testProcess = nil
                self?.settingsModel?.testing = false
                if proc.terminationStatus == 0 {
                    self?.settingsModel?.status = "测试消息已发送，请查看飞书。"
                } else {
                    let tail = String(output.suffix(240))
                    self?.settingsModel?.status = "测试失败：\(tail)"
                }
            }
        }
        testProcess = process
        do {
            try process.run()
        } catch {
            testProcess = nil
            model.testing = false
            model.status = "测试失败：\(error.localizedDescription)"
        }
    }

    private func syncMAASchedule() {
        guard let model = settingsModel else { return }
        let times = model.scheduleTimes
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard !times.isEmpty else {
            model.status = "请至少添加一个定时。"
            return
        }

        let alert = NSAlert()
        alert.messageText = "写入 MAA 定时任务？"
        alert.informativeText = """
        这会修改 MAA 的 MAAScheduledDailyTaskTimer。
        如果 MAA 正在运行，会先退出 MAA，写入后重新启动。
        """
        alert.alertStyle = .warning
        alert.addButton(withTitle: "写入并重启 MAA")
        alert.addButton(withTitle: "取消")
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        saveSettings()
        model.status = "正在写入 MAA 定时…"

        let process = Process()
        process.executableURL = URL(fileURLWithPath: kPython)
        process.arguments = [
            "-B", kRuntime.path,
            "--config", kBaseConfig.path,
            "sync-maa-schedule",
            "--times", times.joined(separator: ","),
        ]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        process.terminationHandler = { [weak self] proc in
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let output = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            DispatchQueue.main.async {
                self?.syncProcess = nil
                if proc.terminationStatus == 0 {
                    self?.settingsModel?.maaSchedule = self?.readMaaScheduleText() ?? ""
                    self?.settingsModel?.status = "MAA 定时已写入并重启。"
                } else {
                    self?.settingsModel?.status = "写入 MAA 定时失败：\(String(output.suffix(240)))"
                }
            }
        }
        syncProcess = process
        do {
            try process.run()
        } catch {
            syncProcess = nil
            model.status = "写入 MAA 定时失败：\(error.localizedDescription)"
        }
    }

    private func startMAA() {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        process.arguments = ["-a", kMaaAppPath]
        do {
            try process.run()
            settingsModel?.status = "已请求启动 MAA。"
        } catch {
            settingsModel?.status = "启动 MAA 失败：\(error.localizedDescription)"
        }
    }

    private func quitMAA() {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", "tell application id \"com.hguandl.MeoAsstMac\" to quit"]
        do {
            try process.run()
            settingsModel?.status = "已请求退出 MAA。"
        } catch {
            settingsModel?.status = "退出 MAA 失败：\(error.localizedDescription)"
        }
    }

    private func readMaaScheduleList() -> [String] {
        let prefs = kFeedDir.appendingPathComponent("prefs.plist")
        if let data = try? Data(contentsOf: prefs),
           let plist = try? PropertyListSerialization.propertyList(
               from: data,
               options: [],
               format: nil
           ) as? [String: Any],
           let raw = plist["MAAScheduledDailyTaskTimer"] {
            var value: Any = raw
            if let text = raw as? String,
               let jsonData = text.data(using: .utf8),
               let parsed = try? JSONSerialization.jsonObject(with: jsonData) {
                value = parsed
            }
            if let items = value as? [[String: Any]] {
                var slots: [String] = []
                for item in items {
                    let enabled = (item["isEnabled"] as? NSNumber)?.boolValue ?? true
                    guard enabled,
                          let hour = (item["hour"] as? NSNumber)?.intValue,
                          let minute = (item["minute"] as? NSNumber)?.intValue
                    else { continue }
                    slots.append(String(format: "%02d:%02d", hour, minute))
                }
                if !slots.isEmpty {
                    return slots.sorted()
                }
            }
        }

        let base = loadJSON(kBaseConfig) ?? [:]
        let maa = base["maa"] as? [String: Any] ?? [:]
        return maa["fallback_times"] as? [String] ?? []
    }

    private func readMaaScheduleText() -> String {
        readMaaScheduleList().joined(separator: " / ")
    }

    private func configuredScheduleList() -> [String] {
        let base = loadJSON(kBaseConfig) ?? [:]
        let maa = base["maa"] as? [String: Any] ?? [:]
        let configured = (maa["schedule_times"] as? [String] ?? [])
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        return configured.isEmpty ? readMaaScheduleList() : configured
    }

    // MARK: menu

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()

        let status = loadJSON(kStatus) ?? [:]
        let state = loadJSON(kStateDir.appendingPathComponent("state.json")) ?? [:]
        let error = (status["error"] as? String) ?? ""
        let source = sourceURL?.path ?? ""
        let watcherRunning = watcher?.isRunning ?? false

        let headline: String
        if source.isEmpty {
            headline = "未授权"
        } else if !error.isEmpty {
            headline = "同步异常"
        } else if !watcherRunning {
            headline = "解析进程未运行"
        } else {
            headline = "运行中"
        }
        addInfo(menu, "MAA 监控 · \(headline)")

        if let last = state["last_run"] as? [String: Any] {
            let slot = last["slot"] as? String ?? ""
            let st = last["status"] as? String ?? ""
            let counts = last["counts"] as? [String: Any] ?? [:]
            let completed = (counts["completed"] as? NSNumber)?.intValue ?? 0
            let total = (counts["total"] as? NSNumber)?.intValue ?? 0
            addInfo(menu, "上次：\(slot) \(statusLabel(st))（\(completed)/\(total)）")
        } else {
            addInfo(menu, "上次：暂无")
        }

        let schedule = configuredScheduleList().joined(separator: " / ")
        if !schedule.isEmpty {
            addInfo(menu, "定时：\(schedule)")
        }

        if !error.isEmpty {
            addInfo(menu, "错误：\(String(error.prefix(80)))")
        }

        menu.addItem(.separator())
        addAction(menu, "设置…", #selector(openSettingsAction))
        addAction(menu, "打开日志", #selector(openLogAction))
        menu.addItem(.separator())
        addAction(menu, "退出", #selector(quitAction))

        updateIcon(error: error, authorized: !source.isEmpty)
    }

    private func addInfo(_ menu: NSMenu, _ title: String) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        menu.addItem(item)
    }

    private func addAction(_ menu: NSMenu, _ title: String, _ selector: Selector) {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        item.target = self
        menu.addItem(item)
    }

    private func updateIcon(error: String, authorized: Bool) {
        if !authorized {
            setSymbolIcon("questionmark.circle")
            return
        }
        if !error.isEmpty {
            setSymbolIcon("exclamationmark.triangle.fill")
            return
        }
        applyIcon()
    }

    private func setSymbolIcon(_ name: String) {
        guard let button = statusItem.button else { return }
        button.title = ""
        if let image = NSImage(systemSymbolName: name, accessibilityDescription: "MAA") {
            image.isTemplate = true
            button.image = image
        }
    }

    private func applyIcon() {
        guard let button = statusItem.button else { return }
        button.image = nil
        button.title = ""

        if iconName == "text" {
            button.title = "MAA"
            return
        }
        if iconName == "maa" {
            let path = "/Applications/MAA.app/Contents/Resources/AppIcon.icns"
            if let image = NSImage(contentsOfFile: path) {
                image.size = NSSize(width: 18, height: 18)
                image.isTemplate = false
                button.image = image
                return
            }
        }

        if let image = NSImage(
            systemSymbolName: iconName,
            accessibilityDescription: "MAA 状态监控"
        ) {
            image.isTemplate = true
            button.image = image
            return
        }
        setSymbolIcon("antenna.radiowaves.left.and.right")
    }

    private func saveIconSetting() {
        writeJSON(["icon": iconName], to: kSettings)
    }

    private func statusLabel(_ status: String) -> String {
        switch status {
        case "success": return "成功"
        case "partial": return "部分失败"
        case "failed": return "失败"
        case "cancelled": return "已取消"
        case "timeout": return "超时"
        case "missed": return "未运行"
        default: return status
        }
    }

    // MARK: actions

    @objc private func setIconAction(_ sender: NSMenuItem) {
        guard let name = sender.representedObject as? String else { return }
        iconName = name
        saveIconSetting()
        applyIcon()
    }

    @objc private func chooseFolderAction() {
        promptForFolder()
    }

    @objc private func openLogAction() {
        NSWorkspace.shared.open(kWatcherLog)
    }

    @objc private func openFeedAction() {
        NSWorkspace.shared.open(kFeedDir)
    }

    @objc private func openConfigAction() {
        NSWorkspace.shared.open(kMenuConfig)
    }

    @objc private func restartAction() {
        stopWatcher()
        mirror?.stop()
        mirror = nil
        if let sourceURL, validateSource(sourceURL) {
            startMonitoring()
        } else {
            promptForFolder()
        }
    }

    @objc private func toggleAutoControlAction() {
        var config = loadJSON(kMenuConfig) ?? [:]
        var policy = config["policy"] as? [String: Any] ?? [:]
        let current = ((policy["auto_start_maa"] as? Bool) ?? false)
            && ((policy["auto_quit_maa"] as? Bool) ?? false)
        policy["auto_start_maa"] = !current
        policy["auto_quit_maa"] = !current
        if policy["auto_start_lead_minutes"] == nil { policy["auto_start_lead_minutes"] = 5 }
        if policy["auto_quit_delay_seconds"] == nil { policy["auto_quit_delay_seconds"] = 60 }
        config["policy"] = policy
        writeJSON(config, to: kMenuConfig)
        stopWatcher()
        startWatcher()
    }

    @objc private func toggleLiveAction() {
        var config = loadJSON(kMenuConfig) ?? [:]
        var runtime = config["runtime"] as? [String: Any] ?? [:]
        let dryRun = (runtime["dry_run"] as? Bool) ?? true
        runtime["dry_run"] = !dryRun
        config["runtime"] = runtime
        writeJSON(config, to: kMenuConfig)
        stopWatcher()
        startWatcher()
    }

    @objc private func openSettingsAction() {
        openSettings()
    }

    @objc private func quitAction() {
        quitting = true
        NSApp.terminate(nil)
    }
}

// MARK: - main

@main
struct MaaNotifyMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
