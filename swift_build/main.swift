import Cocoa
import WebKit

// ── Version & update config ───────────────────────────────────────────────────
let kAppVersion = "1.0.4"
let kGitHubRepo = "gdave3/token-tracker"

// ── Auto Updater ──────────────────────────────────────────────────────────────
class AutoUpdater {
    static let shared = AutoUpdater()

    func checkOnStartup() {
        DispatchQueue.global(qos: .background).asyncAfter(deadline: .now() + 6) {
            self.checkForUpdate()
        }
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
            guard let dmg = assets.first(where: { ($0["name"] as? String)?.hasSuffix(".dmg") == true }),
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
        // Mount DMG and capture plist output to find the mount point
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
        // Fallback: scan /Volumes
        if mountPoint == nil {
            mountPoint = (try? FileManager.default.contentsOfDirectory(atPath: "/Volumes"))?
                .first(where: { $0.lowercased().contains("token") })
                .map { "/Volumes/\($0)" }
        }
        guard let vol = mountPoint else { openFallback(dmg); return }

        // Find the .app inside the volume
        guard let appName = (try? FileManager.default.contentsOfDirectory(atPath: vol))?
                .first(where: { $0.hasSuffix(".app") }) else {
            detach(vol); openFallback(dmg); return
        }
        let srcApp = "\(vol)/\(appName)"

        // Destination: the current running .app bundle
        let destApp = Bundle.main.bundlePath

        // Replace with ditto, strip quarantine, re-sign
        run("/bin/rm",          ["-rf", destApp])
        run("/usr/bin/ditto",   [srcApp, destApp])
        run("/usr/bin/xattr",   ["-cr", destApp])
        run("/usr/bin/codesign",["--deep", "--force", "--sign", "-", destApp])
        detach(vol)

        // Relaunch after 1 s then quit
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

    private func detach(_ mountPoint: String) {
        run("/usr/bin/hdiutil", ["detach", mountPoint, "-quiet"])
    }

    private func openFallback(_ dmg: URL) {
        DispatchQueue.main.async { NSWorkspace.shared.open(dmg) }
    }
}

// ── Data path ────────────────────────────────────────────────────────────────
let logFile = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/TokenTracker/usage_log.json")

// ── Token formatting ─────────────────────────────────────────────────────────
func fmtTok(_ n: Int) -> String {
    if n >= 1_000_000 { return String(format: "%.2fM", Double(n) / 1_000_000) }
    if n >= 1_000     { return String(format: "%.1fK", Double(n) / 1_000) }
    return "\(n)"
}

func todayTokens() -> Int {
    guard let data = try? Data(contentsOf: logFile),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let entries = json["entries"] as? [[String: Any]] else { return 0 }
    let today = ISO8601DateFormatter().string(from: Date()).prefix(10)
    return entries.filter { ($0["ts"] as? String ?? "").hasPrefix(today) }
        .reduce(0) { $0 + ($1["input_tokens"] as? Int ?? 0) + ($1["output_tokens"] as? Int ?? 0) }
}

// ── App Delegate ─────────────────────────────────────────────────────────────
class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {

    var statusItem: NSStatusItem!
    var window: NSWindow?
    var webView: WKWebView?
    var serverProcess: Process?
    var refreshTimer: Timer?

    func applicationDidFinishLaunching(_ n: Notification) {
        NSApp.setActivationPolicy(.regular)

        startPythonServer()
        buildStatusItem()
        AutoUpdater.shared.checkOnStartup()

        // Open dashboard after server warms up
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { self.openDashboard() }

        refreshTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.updateTitle()
        }
    }

    // ── Python server ─────────────────────────────────────────────────────────
    func startPythonServer() {
        let python = "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
        let script = Bundle.main.path(forResource: "server", ofType: "py")!

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = [script]
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError  = FileHandle.nullDevice
        try? proc.run()
        serverProcess = proc
    }

    // ── Menu bar ──────────────────────────────────────────────────────────────
    func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        updateTitle()

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Open Dashboard", action: #selector(openDashboard), keyEquivalent: "d"))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Open Log File", action: #selector(openLog), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Token Tracker", action: #selector(quit), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    func updateTitle() {
        let tok = todayTokens()
        let dot = (serverProcess?.isRunning == true) ? "🟢" : "⚪"
        let label = tok > 0 ? "\(dot) 𝒯 \(fmtTok(tok))" : "\(dot) 𝒯 —"
        DispatchQueue.main.async { self.statusItem.button?.title = label }
    }

    // ── Dashboard window ──────────────────────────────────────────────────────
    @objc func openDashboard() {
        if let w = window {
            w.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            webView?.reload()
            return
        }

        let rect = NSRect(x: 150, y: 150, width: 1100, height: 780)
        let style: NSWindow.StyleMask = [.titled, .closable, .resizable, .miniaturizable]
        let w = NSWindow(contentRect: rect, styleMask: style,
                         backing: .buffered, defer: false)
        w.title = "Token Tracker"
        w.isReleasedWhenClosed = false

        let wv = WKWebView(frame: rect)
        wv.navigationDelegate = self
        wv.uiDelegate = self
        if let url = URL(string: "http://localhost:8765") {
            wv.load(URLRequest(url: url))
        }
        w.contentView = wv
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        window  = w
        webView = wv
    }

    // Open external https:// links in Safari instead of inside WKWebView
    func webView(_ webView: WKWebView,
                 decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.navigationType == .linkActivated,
           let url = action.request.url,
           (url.scheme == "https" || url.scheme == "http"),
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
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    @objc func openLog() {
        NSWorkspace.shared.open(logFile)
    }

    @objc func quit() {
        serverProcess?.terminate()
        NSApp.terminate(nil)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        openDashboard()
        return true
    }

    func applicationWillTerminate(_ n: Notification) {
        refreshTimer?.invalidate()
        serverProcess?.terminate()
    }
}

// ── Entry point ───────────────────────────────────────────────────────────────
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
