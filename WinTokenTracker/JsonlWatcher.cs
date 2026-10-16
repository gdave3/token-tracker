using System.Text.Json;

namespace TokenTracker;

public class JsonlWatcher : IDisposable
{
    private readonly LogManager              _log;
    private readonly string                  _projectsPath;
    private readonly Dictionary<string, long> _filePositions = new();
    private readonly HashSet<string>          _seenRequests  = new();
    private readonly object                   _lock          = new();
    private          FileSystemWatcher?       _fsw;

    public JsonlWatcher(LogManager log, string projectsPath)
    {
        _log          = log;
        _projectsPath = projectsPath;
    }

    // Read all existing JSONL from scratch as "history" entries
    public void LoadHistory()
    {
        if (!Directory.Exists(_projectsPath)) return;
        foreach (var file in Directory.EnumerateFiles(_projectsPath, "*.jsonl", SearchOption.AllDirectories))
        {
            lock (_lock) _filePositions[file] = 0;
            Scan(file, "history");
        }
    }

    // Set all positions to EOF — only new data will be captured
    public void Prime()
    {
        if (!Directory.Exists(_projectsPath)) return;
        foreach (var file in Directory.EnumerateFiles(_projectsPath, "*.jsonl", SearchOption.AllDirectories))
            lock (_lock) _filePositions[file] = new FileInfo(file).Length;
    }

    // Start FSW for live event-driven watching
    public void StartWatching()
    {
        if (!Directory.Exists(_projectsPath)) return;
        _fsw = new FileSystemWatcher(_projectsPath)
        {
            Filter                = "*.jsonl",
            IncludeSubdirectories = true,
            EnableRaisingEvents   = true,
            NotifyFilter          = NotifyFilters.LastWrite | NotifyFilters.FileName
        };
        _fsw.Changed += (_, e) => Scan(e.FullPath, "live");
        _fsw.Created += (_, e) => { lock (_lock) _filePositions[e.FullPath] = 0; Scan(e.FullPath, "live"); };
    }

    private void Scan(string path, string entryType)
    {
        try
        {
            long pos;
            lock (_lock) pos = _filePositions.GetValueOrDefault(path, 0L);

            using var fs     = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            fs.Seek(pos, SeekOrigin.Begin);
            using var reader = new StreamReader(fs);

            string? line;
            while ((line = reader.ReadLine()) != null)
            {
                long newPos = fs.Position;
                lock (_lock) _filePositions[path] = newPos;

                if (string.IsNullOrWhiteSpace(line)) continue;
                try { ProcessLine(line, path, entryType); }
                catch { }
            }
        }
        catch { }
    }

    private void ProcessLine(string line, string path, string entryType)
    {
        var obj = JsonSerializer.Deserialize<JsonElement>(line);

        if (!obj.TryGetProperty("type", out var typeProp) || typeProp.GetString() != "assistant") return;
        if (!obj.TryGetProperty("message", out var msg))   return;
        if (!msg.TryGetProperty("usage", out var usage))   return;
        if (!usage.TryGetProperty("output_tokens", out var outEl) || outEl.GetInt32() == 0) return;

        var requestId = obj.TryGetProperty("requestId", out var rid) ? rid.GetString() ?? "" : "";
        lock (_lock)
        {
            if (!string.IsNullOrEmpty(requestId))
            {
                if (_seenRequests.Contains(requestId)) return;
                _seenRequests.Add(requestId);
            }
        }

        var inp   = usage.TryGetProperty("input_tokens",                out var i)  ? i.GetInt32()  : 0;
        var cc    = usage.TryGetProperty("cache_creation_input_tokens",  out var c)  ? c.GetInt32()  : 0;
        var cr    = usage.TryGetProperty("cache_read_input_tokens",      out var r)  ? r.GetInt32()  : 0;
        var outp  = outEl.GetInt32();
        var model = msg.TryGetProperty("model", out var m) ? m.GetString() ?? "claude-sonnet-4-6" : "claude-sonnet-4-6";
        var ts    = obj.TryGetProperty("timestamp", out var t) ? t.GetString() ?? UtcNow() : UtcNow();
        var stem  = Path.GetFileNameWithoutExtension(path);
        var src   = "claude-code:" + stem[..Math.Min(8, stem.Length)];

        _log.AddEntry(ts, entryType, model, src, inp, cc, cr, outp);
    }

    private static string UtcNow() => DateTime.UtcNow.ToString("o");

    public void Dispose() => _fsw?.Dispose();
}
