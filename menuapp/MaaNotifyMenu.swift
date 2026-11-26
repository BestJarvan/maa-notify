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
                return
            }
        }

        if let data = try? Data(contentsOf: kBookmark), let url = resolveBookmark(data), validateSource(url) {
            sourceURL = url
            startMonitoring()
            return
        }

        if let path = try? String(contentsOf: kBookmarkPath, encoding: .utf8) {
            let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                let url = URL(fileURLWithPath: trimmed)
                if validateSource(url) {
                    sourceURL = url
                    startMonitoring()
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

    private func promptForFolder() {
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

        if let feishu = base["feishu"] {
            existing["feishu"] = feishu
        }
        if existing["policy"] == nil, let policy = base["policy"] {
            existing["policy"] = policy
        }

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

    // MARK: menu

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()

        let status = loadJSON(kStatus) ?? [:]
        let state = loadJSON(kStateDir.appendingPathComponent("state.json")) ?? [:]
        let menuConfig = loadJSON(kMenuConfig) ?? [:]
        let runtime = menuConfig["runtime"] as? [String: Any] ?? [:]
        let dryRun = (runtime["dry_run"] as? Bool) ?? true
        let policy = menuConfig["policy"] as? [String: Any] ?? [:]
        let autoStart = (policy["auto_start_maa"] as? Bool) ?? false
        let autoQuit = (policy["auto_quit_maa"] as? Bool) ?? false
        let autoOn = autoStart && autoQuit
        let autoLead = (policy["auto_start_lead_minutes"] as? NSNumber)?.intValue ?? 5
        let autoQuitDelay = (policy["auto_quit_delay_seconds"] as? NSNumber)?.intValue ?? 60

        let error = (status["error"] as? String) ?? ""
        let source = sourceURL?.path ?? ""
        let watcherRunning = watcher?.isRunning ?? false

        if source.isEmpty {
            addInfo(menu, "MAA 监控：未授权")
            addInfo(menu, "请在菜单里选择 MAA 的 Data 目录")
        } else if !error.isEmpty {
            addInfo(menu, "MAA 监控：同步异常")
            addInfo(menu, error)
        } else {
            addInfo(menu, "MAA 监控：运行中")
        }

        if let lastSyncText = status["lastSync"] as? String,
           let lastSync = ISO8601DateFormatter().date(from: lastSyncText) {
            addInfo(menu, "上次同步：\(formatAge(lastSync))")
        } else {
            addInfo(menu, "上次同步：等待中")
        }

        if let last = state["last_run"] as? [String: Any] {
            let date = last["date"] as? String ?? ""
            let slot = last["slot"] as? String ?? ""
            let st = last["status"] as? String ?? ""
            let counts = last["counts"] as? [String: Any] ?? [:]
            let completed = counts["completed"] as? Int ?? 0
            let total = counts["total"] as? Int ?? 0
            addInfo(menu, "上次结果：\(date) \(slot) \(statusLabel(st))（\(completed)/\(total)）")
        } else {
            addInfo(menu, "上次结果：暂无")
        }

        addInfo(menu, "解析进程：\(watcherRunning ? "运行中" : "未运行")")
        addInfo(menu, "飞书推送：\(dryRun ? "关闭（dry-run）" : "已开启")")
        addInfo(
            menu,
            "自动启停 MAA：\(autoOn ? "已开启" : "已关闭")"
                + (autoOn ? "（提前 \(autoLead) 分钟启动，结束后 \(autoQuitDelay) 秒退出）" : "")
        )

        menu.addItem(.separator())

        let iconMenu = NSMenu()
        let icons: [(String, String)] = [
            ("antenna", "天线（默认）"),
            ("gamecontroller", "手柄"),
            ("shield.lefthalf.filled", "盾牌"),
            ("bolt.horizontal.circle", "闪电"),
            ("waveform", "波形"),
            ("text", "MAA 文字"),
            ("maa", "MAA App 图标"),
        ]
        for (name, title) in icons {
            let item = NSMenuItem(title: title, action: #selector(setIconAction(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = name
            item.state = (name == iconName) ? .on : .off
            iconMenu.addItem(item)
        }
        let iconItem = NSMenuItem(title: "更换图标", action: nil, keyEquivalent: "")
        iconItem.submenu = iconMenu
        menu.addItem(iconItem)

        addAction(menu, "重新选择 MAA 目录…", #selector(chooseFolderAction))
        addAction(menu, "打开 watcher 日志", #selector(openLogAction))
        addAction(menu, "打开 feed 目录", #selector(openFeedAction))
        addAction(menu, "打开配置", #selector(openConfigAction))
        addAction(menu, "重启监控", #selector(restartAction))
        addAction(menu, dryRun ? "开启飞书推送" : "关闭飞书推送", #selector(toggleLiveAction))
        addAction(
            menu,
            autoOn ? "关闭自动启停 MAA" : "开启自动启停 MAA",
            #selector(toggleAutoControlAction)
        )
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

    @objc private func quitAction() {
        quitting = true
        NSApp.terminate(nil)
    }
}

// MARK: - main

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
