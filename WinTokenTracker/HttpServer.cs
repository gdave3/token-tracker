using System.Net;

namespace TokenTracker;

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
