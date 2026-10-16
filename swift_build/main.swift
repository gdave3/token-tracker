import Cocoa
import WebKit

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
class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {

    var statusItem: NSStatusItem!
    var window: NSWindow?
    var webView: WKWebView?
    var serverProcess: Process?
    var refreshTimer: Timer?

    func applicationDidFinishLaunching(_ n: Notification) {
        NSApp.setActivationPolicy(.regular)

        startPythonServer()
        buildStatusItem()

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
        if let url = URL(string: "http://localhost:8765") {
            wv.load(URLRequest(url: url))
        }
        w.contentView = wv
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        window  = w
        webView = wv
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
