// MacTokenTracker — pure Swift menu bar app, no Python dependency
import Cocoa
import WebKit
import Network
import Foundation

// MARK: - Version & update config

let kAppVersion = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String)
               ?? (Bundle.main.infoDictionary?["CFBundleVersion"] as? String)
               ?? "1.0.9"
let kGitHubRepo = "gdave3/token-tracker"

// MARK: - Auto Updater

class AutoUpdater {
    static let shared = AutoUpdater()

    func checkOnStartup() {
        DispatchQueue.global(qos: .background).asyncAfter(deadline: .now() + 6) {
            self.checkForUpdate()
        }
        // Re-check every 4 hours for long-running sessions
        DispatchQueue.main.asyncAfter(deadline: .now() + 6) {
            Timer.scheduledTimer(withTimeInterval: 4 * 3600, repeats: true) { _ in
                DispatchQueue.global(qos: .background).async { self.checkForUpdate() }
            }
        }
    }

    func checkNow() {
        DispatchQueue.global(qos: .background).async { self.checkForUpdate() }
    }

    private func checkForUpdate() {
        guard let url = URL(string: "https://api.github.com/repos/\(kGitHubRepo)/releases/latest") else { return }
        var req = URLRequest(url: url)
        req.setValue("application/vnd.github.v3+json", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 10
        URLSession.shared.dataTask(with: req) { [weak self] data, _, _ in
            guard let self,
                  let data,
                  let json  = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tag   = json["tag_name"] as? String else { return }
            let latest = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
            guard self.isNewer(latest, than: kAppVersion) else { return }
            let assets = json["assets"] as? [[String: Any]] ?? []
            guard let dmg   = assets.first(where: { ($0["name"] as? String)?.hasSuffix(".dmg") == true }),
                  let dlStr = dmg["browser_download_url"] as? String,
                  let dlURL = URL(string: dlStr) else { return }
            self.download(version: latest, from: dlURL)
        }.resume()
    }

    private func isNewer(_ a: String, than b: String) -> Bool {
        let av = a.split(separator: ".").compactMap { Int($0) }
        let bv = b.split(separator: ".").compactMap { Int($0) }
        for i in 0..<max(av.count, bv.count) {
            let ai = i < av.count ? av[i] : 0
            let bi = i < bv.count ? bv[i] : 0
            if ai != bi { return ai > bi }
        }
        return false
    }

    private func download(version: String, from url: URL) {
        let dest = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("TokenTracker-\(version).dmg")
        if FileManager.default.fileExists(atPath: dest.path) {
            DispatchQueue.main.async { self.promptInstall(version: version, dmg: dest) }
            return
        }
        URLSession.shared.downloadTask(with: url) { tmp, _, err in
            guard let tmp, err == nil else { return }
            try? FileManager.default.removeItem(at: dest)
            try? FileManager.default.moveItem(at: tmp, to: dest)
            DispatchQueue.main.async { self.promptInstall(version: version, dmg: dest) }
        }.resume()
    }

    private func promptInstall(version: String, dmg: URL) {
        let alert = NSAlert()
        alert.messageText = "Token Tracker \(version) ready"
        alert.informativeText = "A new version was downloaded. Install it now and restart?"
        alert.addButton(withTitle: "Install & Restart")
        alert.addButton(withTitle: "Later")
        alert.alertStyle = .informational
        if alert.runModal() == .alertFirstButtonReturn {
            install(version: version, dmg: dmg)
        }
    }

    private func install(version: String, dmg: URL) {
        let hdi = Process(); let pipe = Pipe()
        hdi.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
        hdi.arguments = ["attach", dmg.path, "-nobrowse", "-quiet", "-plist"]
        hdi.standardOutput = pipe
        hdi.standardError  = FileHandle.nullDevice
        try? hdi.run(); hdi.waitUntilExit()

        let plistData = pipe.fileHandleForReading.readDataToEndOfFile()
        var mountPoint: String?
        if let plist = try? PropertyListSerialization.propertyList(from: plistData, format: nil) as? [String: Any],
           let entities = plist["system-entities"] as? [[String: Any]] {
            mountPoint = entities.compactMap { $0["mount-point"] as? String }.first
        }
        if mountPoint == nil {
            mountPoint = (try? FileManager.default.contentsOfDirectory(atPath: "/Volumes"))?
                .first(where: { $0.lowercased().contains("token") })
                .map { "/Volumes/\($0)" }
        }
        guard let vol = mountPoint else { openFallback(dmg); return }

        guard let appName = (try? FileManager.default.contentsOfDirectory(atPath: vol))?
                .first(where: { $0.hasSuffix(".app") }) else { detach(vol); openFallback(dmg); return }
        let srcApp  = "\(vol)/\(appName)"
        let destApp = Bundle.main.bundlePath

        run("/bin/rm",           ["-rf", destApp])
        run("/usr/bin/ditto",    [srcApp, destApp])
        run("/usr/bin/xattr",    ["-cr", destApp])
        run("/usr/bin/codesign", ["--deep", "--force", "--sign", "-", destApp])
        detach(vol)

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", "sleep 1 && open '\(destApp)'"]
        try? p.run()
        NSApp.terminate(nil)
    }

    private func run(_ path: String, _ args: [String]) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError  = FileHandle.nullDevice
        try? p.run(); p.waitUntilExit()
    }
    private func detach(_ mountPoint: String) { run("/usr/bin/hdiutil", ["detach", mountPoint, "-quiet"]) }
    private func openFallback(_ dmg: URL) { DispatchQueue.main.async { NSWorkspace.shared.open(dmg) } }
}

// MARK: - Constants & Paths

let kPort: UInt16 = 8765
let kPollInterval: TimeInterval = 2.0
let kMenuRefreshInterval: TimeInterval = 10.0

let homeDir         = FileManager.default.homeDirectoryForCurrentUser
let claudeProjects  = homeDir.appendingPathComponent(".claude/projects")
let logDir          = homeDir.appendingPathComponent("Library/Application Support/TokenTracker")
let logFilePath     = logDir.appendingPathComponent("usage_log.json")
let binaryDir       = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
let dashboardPath   = binaryDir.appendingPathComponent("token-dashboard.html")

// MARK: - Cost Calculation

typealias Rates = (inp: Double, out: Double, cc: Double, cr: Double)
let modelRates: [String: Rates] = [
    "claude-opus-5-5":   (15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-5-5": ( 3.0, 15.0,  3.75, 0.30),
    "claude-haiku-5-5":  ( 0.80, 4.0,  1.00, 0.08),
    "claude-opus-4-5":   (15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-4-6": ( 3.0, 15.0,  3.75, 0.30),
    "claude-haiku-4-5":  ( 0.80, 4.0,  1.00, 0.08),
]

func costUSD(inp: Int, out: Int, cc: Int, cr: Int, model: String) -> Double {
    let r = modelRates[model] ?? (3.0, 15.0, 3.75, 0.30)
    return (Double(inp) * r.inp + Double(out) * r.out
          + Double(cc)  * r.cc  + Double(cr)  * r.cr) / 1_000_000
}

// MARK: - Token Log

class LogManager {
    private let lock = NSLock()
    private var entries: [[String: Any]] = []
    private var totals: [String: Any] = ["input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0]

    init() {
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        load()
    }

    func load() {
        guard let data = try? Data(contentsOf: logFilePath),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        lock.lock()
        entries = json["entries"] as? [[String: Any]] ?? []
        totals  = json["totals"]  as? [String: Any]   ?? totals
        lock.unlock()
    }

    func save() {
        lock.lock()
        let snapshot: [String: Any] = ["version": 1, "entries": entries, "totals": totals]
        lock.unlock()
        guard let data = try? JSONSerialization.data(withJSONObject: snapshot, options: [.prettyPrinted, .sortedKeys]) else { return }
        try? data.write(to: logFilePath)
    }

    func readBytes() -> Data { (try? Data(contentsOf: logFilePath)) ?? Data("{}".utf8) }

    func addEntry(ts: String, type: String, model: String, note: String,
                  inp: Int, cc: Int, cr: Int, out: Int) {
        let compute = inp + cc + out
        let cost = costUSD(inp: inp, out: out, cc: cc, cr: cr, model: model)
        let entry: [String: Any] = [
            "ts": ts, "type": type, "model": model, "note": note,
            "input_tokens": inp, "cache_creation_tokens": cc,
            "cache_read_tokens": cr, "output_tokens": out,
            "compute_tokens": compute, "cost_usd": cost
        ]
        lock.lock()
        entries.append(entry)
        let prevIn   = totals["input_tokens"]  as? Int    ?? 0
        let prevOut  = totals["output_tokens"] as? Int    ?? 0
        let prevCost = totals["cost_usd"]      as? Double ?? 0.0
        totals["input_tokens"]  = prevIn + inp
        totals["output_tokens"] = prevOut + out
        totals["cost_usd"]      = (prevCost + cost).rounded(decimals: 8)
        lock.unlock()
        save()
    }

    var hasEntries: Bool { lock.lock(); defer { lock.unlock() }; return !entries.isEmpty }

    func todayComputeTokens() -> Int {
        let today = String(ISO8601DateFormatter().string(from: Date()).prefix(10))
        lock.lock()
        let result = entries
            .filter { ($0["ts"] as? String ?? "").hasPrefix(today) }
            .reduce(0) { $0 + ($1["compute_tokens"] as? Int ?? 0) }
        lock.unlock()
        return result
    }
}

extension Double {
    func rounded(decimals: Int) -> Double {
        let f = pow(10.0, Double(decimals))
        return (self * f).rounded() / f
    }
}

// MARK: - Indexing Progress

class IndexingProgress {
    static let shared = IndexingProgress()
    private let lock = NSLock()
    private var _total = 0
    private var _done  = 0
    private var _finished = false

    func start(total: Int) {
        lock.lock(); _total = total; _done = 0; _finished = false; lock.unlock()
    }
    func advance() {
        lock.lock(); _done += 1; lock.unlock()
    }
    func finish() {
        lock.lock(); _finished = true; lock.unlock()
    }
    func json() -> Data {
        lock.lock()
        let t = _total; let d = _done; let f = _finished
        lock.unlock()
        let pct = t > 0 ? Double(d) / Double(t) : 1.0
        let s = "{\"indexing\":\(!f),\"done\":\(d),\"total\":\(t),\"pct\":\(String(format:"%.3f",pct))}"
        return Data(s.utf8)
    }
}

// MARK: - Minimal HTTP Server

class TokenHTTPServer {
    private var listener: NWListener?
    private let logManager: LogManager

    init(logManager: LogManager) { self.logManager = logManager }

    func start() {
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        guard let port = NWEndpoint.Port(rawValue: kPort),
              let l = try? NWListener(using: params, on: port) else { return }
        listener = l
        l.newConnectionHandler = { [weak self] conn in self?.handle(conn) }
        l.start(queue: .global(qos: .utility))
    }

    private func handle(_ conn: NWConnection) {
        conn.start(queue: .global(qos: .utility))
        conn.receive(minimumIncompleteLength: 1, maximumLength: 8192) { [weak self] data, _, _, _ in
            guard let self, let data, !data.isEmpty else { conn.cancel(); return }
            let req = String(data: data, encoding: .utf8) ?? ""
            self.respond(req, conn: conn)
        }
    }

    private func respond(_ request: String, conn: NWConnection) {
        let path = request.components(separatedBy: "\r\n").first?
            .components(separatedBy: " ").dropFirst().first ?? "/"

        let (body, ct): (Data, String)
        if path == "/usage_log.json" {
            body = logManager.readBytes()
            ct   = "application/json"
        } else if path == "/status" {
            body = IndexingProgress.shared.json()
            ct   = "application/json"
        } else {
            body = (try? Data(contentsOf: dashboardPath))
                ?? Data("<h1>Dashboard not found. Place token-dashboard.html next to this binary.</h1>".utf8)
            ct = "text/html; charset=utf-8"
        }

        let header = "HTTP/1.1 200 OK\r\nContent-Type: \(ct)\r\nContent-Length: \(body.count)\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n"
        var response = Data(header.utf8)
        response.append(body)
        conn.send(content: response, completion: .contentProcessed { _ in conn.cancel() })
    }
}

// MARK: - JSONL Watcher

class JsonlWatcher {
    private var filePositions: [String: UInt64] = [:]
    private var seenRequests: Set<String> = []
    private let lock = NSLock()
    private let logManager: LogManager
    private let positionsFile = logDir.appendingPathComponent("jsonl_positions.json")
    private let isoFmt: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    init(logManager: LogManager) {
        self.logManager = logManager
        loadPositions()
    }

    // Persist file positions so restarts never re-process already-seen bytes
    private func loadPositions() {
        guard let data = try? Data(contentsOf: positionsFile),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        lock.lock()
        for (k, v) in json { if let n = v as? UInt64 { filePositions[k] = n } }
        lock.unlock()
    }

    private func savePositions() {
        lock.lock()
        let snapshot = filePositions.mapValues { $0 as Any }
        lock.unlock()
        guard let data = try? JSONSerialization.data(withJSONObject: snapshot) else { return }
        try? data.write(to: positionsFile)
    }

    // Seek all NEW (unseen) files to EOF without reading — skip history on first encounter
    func prime() {
        forEachJsonl { url in
            self.lock.lock()
            let known = self.filePositions[url.path] != nil
            self.lock.unlock()
            guard !known else { return }
            guard let fh = try? FileHandle(forReadingFrom: url) else { return }
            let end = (try? fh.seekToEnd()) ?? 0
            try? fh.close()
            self.lock.lock(); self.filePositions[url.path] = end; self.lock.unlock()
        }
        savePositions()
        IndexingProgress.shared.finish()
    }

    // Scan all existing JSONL files as history, leave positions at EOF
    func loadHistory() {
        var urls: [URL] = []
        forEachJsonl { urls.append($0) }
        IndexingProgress.shared.start(total: urls.count)
        for url in urls {
            filePositions[url.path] = 0
            scan(url, entryType: "history")
            IndexingProgress.shared.advance()
        }
        IndexingProgress.shared.finish()
        savePositions()
    }

    // Poll — called on timer; scans new bytes in existing files + picks up new files
    func poll() {
        forEachJsonl { url in
            self.lock.lock()
            let known = self.filePositions[url.path] != nil
            self.lock.unlock()
            if !known {
                // New file encountered during polling — seek to EOF so we only get future data
                guard let fh = try? FileHandle(forReadingFrom: url) else { return }
                let end = (try? fh.seekToEnd()) ?? 0
                try? fh.close()
                self.lock.lock(); self.filePositions[url.path] = end; self.lock.unlock()
                return
            }
            self.scan(url, entryType: "live")
        }
        savePositions()
    }

    private func forEachJsonl(_ body: (URL) -> Void) {
        guard let enumerator = FileManager.default.enumerator(
            at: claudeProjects,
            includingPropertiesForKeys: [.fileSizeKey],
            options: [.skipsHiddenFiles]
        ) else { return }
        for case let url as URL in enumerator where url.pathExtension == "jsonl" {
            body(url)
        }
    }

    private func scan(_ url: URL, entryType: String) {
        lock.lock()
        let pos = filePositions[url.path] ?? 0
        lock.unlock()

        guard let fh = try? FileHandle(forReadingFrom: url) else { return }
        defer { try? fh.close() }
        try? fh.seek(toOffset: pos)
        let newData = fh.availableData
        let newPos  = fh.offsetInFile
        guard !newData.isEmpty else { return }

        lock.lock()
        filePositions[url.path] = newPos
        lock.unlock()

        guard let text = String(data: newData, encoding: .utf8) else { return }
        let stem = url.deletingPathExtension().lastPathComponent
        let source = "claude-code:\(stem.prefix(8))"

        for line in text.components(separatedBy: "\n") {
            let t = line.trimmingCharacters(in: .whitespaces)
            guard !t.isEmpty,
                  let d   = t.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { continue }

            guard obj["type"] as? String == "assistant",
                  let msg   = obj["message"] as? [String: Any],
                  let usage = msg["usage"]   as? [String: Any],
                  let outTok = usage["output_tokens"] as? Int, outTok > 0 else { continue }

            let requestId = obj["requestId"] as? String ?? ""
            lock.lock()
            if !requestId.isEmpty {
                if seenRequests.contains(requestId) { lock.unlock(); continue }
                seenRequests.insert(requestId)
            }
            lock.unlock()

            let inp   = usage["input_tokens"]                  as? Int ?? 0
            let cc    = usage["cache_creation_input_tokens"]   as? Int ?? 0
            let cr    = usage["cache_read_input_tokens"]       as? Int ?? 0
            let model = msg["model"] as? String ?? "claude-sonnet-4-6"
            let ts    = obj["timestamp"] as? String ?? isoFmt.string(from: Date())

            logManager.addEntry(ts: ts, type: entryType, model: model, note: source,
                                inp: inp, cc: cc, cr: cr, out: outTok)
        }
    }
}

// MARK: - Token Formatting

func fmtTok(_ n: Int) -> String {
    if n >= 1_000_000 { return String(format: "%.2fM", Double(n) / 1_000_000) }
    if n >= 1_000     { return String(format: "%.1fK", Double(n) / 1_000) }
    return "\(n)"
}

// MARK: - App Delegate

class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    var statusItem: NSStatusItem!
    var window: NSWindow?
    var webView: WKWebView?
    var refreshTimer: Timer?
    var pollTimer: Timer?

    let logManager  = LogManager()
    var httpServer: TokenHTTPServer!
    var watcher: JsonlWatcher!

    func applicationDidFinishLaunching(_ n: Notification) {
        httpServer = TokenHTTPServer(logManager: logManager)
        watcher    = JsonlWatcher(logManager: logManager)

        // First run (no positions file): backfill history from JSONL files.
        // Subsequent runs: positions loaded from disk — only new bytes are processed.
        // New JSONL files encountered mid-session are primed to EOF (not backfilled).
        DispatchQueue.global(qos: .utility).async {
            if self.logManager.hasEntries {
                self.watcher.prime()   // skip history for any new files not yet in positions file
            } else {
                self.watcher.loadHistory()  // first ever run — backfill everything
            }
            DispatchQueue.main.async { self.updateTitle() }
        }

        httpServer.start()
        buildStatusItem()
        AutoUpdater.shared.checkOnStartup()

        // Open dashboard on launch
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.openDashboard() }

        pollTimer = Timer.scheduledTimer(withTimeInterval: kPollInterval, repeats: true) { [weak self] _ in
            DispatchQueue.global(qos: .utility).async { self?.watcher.poll() }
        }
        refreshTimer = Timer.scheduledTimer(withTimeInterval: kMenuRefreshInterval, repeats: true) { [weak self] _ in
            self?.updateTitle()
        }
    }

    func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        updateTitle()

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Open Dashboard", action: #selector(openDashboard), keyEquivalent: "d"))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Check for Updates…", action: #selector(checkForUpdates), keyEquivalent: "u"))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Open Log File", action: #selector(openLog), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Token Tracker", action: #selector(quit), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    func updateTitle() {
        let tok = logManager.todayComputeTokens()
        let label = tok > 0 ? "𝒯 \(fmtTok(tok))" : "𝒯 —"
        DispatchQueue.main.async { self.statusItem.button?.title = label }
    }

    @objc func openDashboard() {
        if let w = window {
            w.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            webView?.reload()
            return
        }
        let rect  = NSRect(x: 150, y: 150, width: 1100, height: 780)
        let style: NSWindow.StyleMask = [.titled, .closable, .resizable, .miniaturizable]
        let w = NSWindow(contentRect: rect, styleMask: style, backing: .buffered, defer: false)
        w.title = "Token Tracker"
        w.isReleasedWhenClosed = false
        // Show app icon in the title bar proxy icon button
        w.representedURL = URL(string: "http://localhost:\(kPort)")
        if let iconBtn = w.standardWindowButton(.documentIconButton) {
            iconBtn.image = NSApp.applicationIconImage
        }

        let wv = WKWebView(frame: rect)
        wv.navigationDelegate = self
        wv.uiDelegate = self
        wv.load(URLRequest(url: URL(string: "http://localhost:\(kPort)")!))
        w.contentView = wv
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        window  = w
        webView = wv
    }

    // Open https:// links in the default browser instead of inside WKWebView
    func webView(_ webView: WKWebView,
                 decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.navigationType == .linkActivated,
           let url = action.request.url,
           url.scheme == "https" || url.scheme == "http",
           url.host != "localhost" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        } else {
            decisionHandler(.allow)
        }
    }

    // Handle target="_blank" links
    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    @objc func openLog() { NSWorkspace.shared.open(logFilePath) }

    @objc func checkForUpdates() { AutoUpdater.shared.checkNow() }

    @objc func quit() { NSApp.terminate(nil) }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        openDashboard(); return true
    }

    func applicationWillTerminate(_ n: Notification) {
        refreshTimer?.invalidate()
        pollTimer?.invalidate()
    }
}

// MARK: - Entry Point
let app      = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
