using System.Text.Json;
using System.Text.Json.Serialization;

namespace TokenTracker;

public class TokenEntry
{
    public string ts                    { get; set; } = "";
    public string type                  { get; set; } = "";
    public string model                 { get; set; } = "";
    public string note                  { get; set; } = "";
    public int    input_tokens          { get; set; }
    public int    cache_creation_tokens { get; set; }
    public int    cache_read_tokens     { get; set; }
    public int    output_tokens         { get; set; }
    public int    compute_tokens        { get; set; }
    public double cost_usd              { get; set; }
}

public class TokenTotals
{
    public int    input_tokens  { get; set; }
    public int    output_tokens { get; set; }
    public double cost_usd      { get; set; }
}

public class TokenLog
{
    public int              version { get; set; } = 1;
    public List<TokenEntry> entries { get; set; } = new();
    public TokenTotals      totals  { get; set; } = new();
}

public class LogManager
{
    private readonly string   _logPath;
    private readonly object   _lock = new();
    private          TokenLog _log  = new();

    private static readonly Dictionary<string, (double inp, double outp, double cc, double cr)> Rates = new()
    {
        ["claude-opus-5-5"]   = (15.0, 75.0, 18.75, 1.50),
        ["claude-sonnet-5-5"] = ( 3.0, 15.0,  3.75, 0.30),
        ["claude-haiku-5-5"]  = ( 0.80, 4.0,  1.00, 0.08),
        ["claude-opus-4-5"]   = (15.0, 75.0, 18.75, 1.50),
        ["claude-sonnet-4-6"] = ( 3.0, 15.0,  3.75, 0.30),
        ["claude-haiku-4-5"]  = ( 0.80, 4.0,  1.00, 0.08),
    };

    private static readonly JsonSerializerOptions JsonOpts = new() { WriteIndented = true };

    public LogManager(string logPath)
    {
        _logPath = logPath;
        Directory.CreateDirectory(Path.GetDirectoryName(logPath)!);
        Load();
    }

    private void Load()
    {
        if (!File.Exists(_logPath)) return;
        try
        {
            var json = File.ReadAllText(_logPath);
            var log  = JsonSerializer.Deserialize<TokenLog>(json);
            if (log != null) { lock (_lock) _log = log; }
        }
        catch { }
    }

    public void Save()
    {
        TokenLog snapshot;
        lock (_lock) snapshot = _log;
        try { File.WriteAllText(_logPath, JsonSerializer.Serialize(snapshot, JsonOpts)); }
        catch { }
    }

    public byte[] ReadBytes()
        => File.Exists(_logPath) ? File.ReadAllBytes(_logPath) : "{}"u8.ToArray();

    private double CostUsd(int inp, int outp, int cc, int cr, string model)
    {
        var r = Rates.GetValueOrDefault(model, (3.0, 15.0, 3.75, 0.30));
        return (inp * r.inp + outp * r.outp + cc * r.cc + cr * r.cr) / 1_000_000;
    }

    public void AddEntry(string ts, string type, string model, string note,
                         int inp, int cc, int cr, int outp)
    {
        var compute = inp + cc + outp;
        var cost    = CostUsd(inp, outp, cc, cr, model);
        var entry   = new TokenEntry
        {
            ts = ts, type = type, model = model, note = note,
            input_tokens = inp, cache_creation_tokens = cc,
            cache_read_tokens = cr, output_tokens = outp,
            compute_tokens = compute, cost_usd = cost
        };
        lock (_lock)
        {
            _log.entries.Add(entry);
            _log.totals.input_tokens  += inp;
            _log.totals.output_tokens += outp;
            _log.totals.cost_usd       = Math.Round(_log.totals.cost_usd + cost, 8);
        }
        Save();
    }

    public (int tokens, double cost) TodayStats()
    {
        var today = DateTime.UtcNow.ToString("yyyy-MM-dd");
        lock (_lock)
        {
            var e = _log.entries.Where(e => e.ts.StartsWith(today)).ToList();
            return (e.Sum(x => x.compute_tokens), e.Sum(x => x.cost_usd));
        }
    }
}
