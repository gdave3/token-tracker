using Microsoft.Web.WebView2.WinForms;

namespace TokenTracker;

public class TrayApp : ApplicationContext
{
    private readonly NotifyIcon       _tray;
    private          Form?            _dashboardForm;
    private          WebView2?        _webView;
    private readonly LogManager       _logManager;
    private readonly TokenHttpServer  _httpServer;
    private readonly JsonlWatcher     _watcher;
    private readonly System.Windows.Forms.Timer _titleTimer;

    private const int Port = 8765;

    public TrayApp()
    {
        var appData       = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        var logPath       = Path.Combine(appData, "TokenTracker", "usage_log.json");
        var dashboardPath = Path.Combine(AppContext.BaseDirectory, "token-dashboard.html");
        var claudeProjects = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".claude", "projects");

        _logManager = new LogManager(logPath);
        _httpServer = new TokenHttpServer(_logManager, dashboardPath);
        _watcher    = new JsonlWatcher(_logManager, claudeProjects);

        // Backfill history on a background thread, then start live watching
        Task.Run(() =>
        {
            _watcher.LoadHistory();
            _watcher.Prime();
            _watcher.StartWatching();
        });

        _httpServer.Start(Port);

        // System tray icon
        _tray = new NotifyIcon
        {
            Icon    = SystemIcons.Application,
            Visible = true,
            Text    = "Token Tracker"
        };

        var menu = new ContextMenuStrip();
        menu.Items.Add("Open Dashboard",      null, (_, _) => OpenDashboard());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Check for Updates…",  null, (_, _) => AutoUpdater.Instance.CheckNow());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Open Log File",       null, (_, _) => OpenLog(logPath));
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Quit",                null, (_, _) => ExitApp());
        _tray.ContextMenuStrip = menu;
        _tray.DoubleClick     += (_, _) => OpenDashboard();

        AutoUpdater.Instance.CheckOnStartup();

        // Refresh tray tooltip every 10 seconds
        _titleTimer = new System.Windows.Forms.Timer { Interval = 10_000 };
        _titleTimer.Tick += (_, _) => UpdateTrayText();
        _titleTimer.Start();
        UpdateTrayText();

        // Open dashboard on startup
        OpenDashboard();
    }

    private static string FmtTok(int n) =>
        n >= 1_000_000 ? $"{n / 1_000_000.0:F2}M" :
        n >= 1_000     ? $"{n / 1_000.0:F1}K"     : n.ToString();

    private void UpdateTrayText()
    {
        var (tok, _) = _logManager.TodayStats();
        _tray.Text = tok > 0 ? $"Token Tracker — {FmtTok(tok)} today" : "Token Tracker";
    }

    private void OpenDashboard()
    {
        if (_dashboardForm != null && !_dashboardForm.IsDisposed)
        {
            _dashboardForm.BringToFront();
            _dashboardForm.WindowState = FormWindowState.Normal;
            return;
        }

        _dashboardForm = new Form
        {
            Text          = "Token Tracker",
            Width         = 1100,
            Height        = 780,
            StartPosition = FormStartPosition.CenterScreen,
            MinimumSize   = new Size(800, 600),
        };
        _dashboardForm.FormClosed += (_, _) => { _dashboardForm = null; _webView = null; };

        _webView = new WebView2 { Dock = DockStyle.Fill };
        _dashboardForm.Controls.Add(_webView);
        _dashboardForm.Show();

        // Navigate after WebView2 environment is ready
        _webView.EnsureCoreWebView2Async().ContinueWith(_ =>
            _webView.Invoke(() => _webView.CoreWebView2.Navigate($"http://localhost:{Port}/")));
    }

    private static void OpenLog(string logPath)
    {
        if (File.Exists(logPath))
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(logPath) { UseShellExecute = true });
    }

    private void ExitApp()
    {
        _tray.Visible = false;
        _watcher.Dispose();
        _titleTimer.Dispose();
        Application.Exit();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) { _tray.Dispose(); _watcher.Dispose(); _titleTimer.Dispose(); }
        base.Dispose(disposing);
    }
}
