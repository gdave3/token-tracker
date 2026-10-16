using System.Net;
using System.Text;

namespace TokenTracker;

public class IndexingProgress
{
    public static readonly IndexingProgress Instance = new();
    private readonly object _lock = new();
    private int _total, _done;
    private bool _finished;

    public void Start(int total)  { lock (_lock) { _total = total; _done = 0; _finished = false; } }
    public void Advance()         { lock (_lock) _done++; }
    public void Finish()          { lock (_lock) _finished = true; }
    public string ToJson()
    {
        lock (_lock)
        {
            var pct = _total > 0 ? (double)_done / _total : 1.0;
            return $"{{\"indexing\":{(!_finished ? "true" : "false")},\"done\":{_done},\"total\":{_total},\"pct\":{pct:F3}}}";
        }
    }
}

public class TokenHttpServer
{
    private readonly LogManager  _log;
    private readonly string      _dashboardPath;
    private          HttpListener? _listener;

    public TokenHttpServer(LogManager log, string dashboardPath)
    {
        _log           = log;
        _dashboardPath = dashboardPath;
    }

    public void Start(int port = 8765)
    {
        _listener = new HttpListener();
        _listener.Prefixes.Add($"http://localhost:{port}/");
        try { _listener.Start(); }
        catch { return; }
        Task.Run(ListenLoop);
    }

    private async Task ListenLoop()
    {
        while (_listener?.IsListening == true)
        {
            try
            {
                var ctx = await _listener.GetContextAsync();
                _ = Task.Run(() => Handle(ctx));
            }
            catch { break; }
        }
    }

    private void Handle(HttpListenerContext ctx)
    {
        try
        {
            var path = ctx.Request.Url?.AbsolutePath ?? "/";

            byte[] body;
            string contentType;

            if (path == "/usage_log.json")
            {
                body        = _log.ReadBytes();
                contentType = "application/json";
            }
            else if (path == "/status")
            {
                body        = Encoding.UTF8.GetBytes(IndexingProgress.Instance.ToJson());
                contentType = "application/json";
            }
            else
            {
                body        = File.Exists(_dashboardPath)
                                ? File.ReadAllBytes(_dashboardPath)
                                : "<h1>Dashboard not found. Place token-dashboard.html next to TokenTracker.exe</h1>"u8.ToArray();
                contentType = "text/html; charset=utf-8";
            }

            ctx.Response.ContentType     = contentType;
            ctx.Response.ContentLength64 = body.Length;
            ctx.Response.AddHeader("Access-Control-Allow-Origin", "*");
            ctx.Response.OutputStream.Write(body);
            ctx.Response.Close();
        }
        catch { }
    }
}
