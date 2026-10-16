using System.Diagnostics;
using System.IO.Compression;
using System.Text.Json;

namespace TokenTracker;

public class AutoUpdater
{
    public static readonly AutoUpdater Instance = new();

    private const string AppVersion = "1.0.15";
    private const string GitHubRepo = "gdave3/token-tracker";

    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(15) };

    public void CheckOnStartup()
    {
        // Initial check 6s after launch
        Task.Delay(6_000).ContinueWith(_ => CheckAsync());
        // Re-check every 4 hours
        var timer = new System.Threading.Timer(_ => CheckAsync(), null,
            TimeSpan.FromHours(4), TimeSpan.FromHours(4));
        // Keep timer alive
        GC.KeepAlive(timer);
    }

    public void CheckNow() => Task.Run(CheckAsync);

    private async Task CheckAsync()
    {
        try
        {
            var req = new HttpRequestMessage(HttpMethod.Get,
                $"https://api.github.com/repos/{GitHubRepo}/releases/latest");
            req.Headers.Add("Accept", "application/vnd.github.v3+json");
            req.Headers.Add("User-Agent", "TokenTracker");
            var res  = await _http.SendAsync(req);
            if (!res.IsSuccessStatusCode) return;
            var json = await res.Content.ReadAsStringAsync();
            var doc  = JsonDocument.Parse(json);
            var tag  = doc.RootElement.GetProperty("tag_name").GetString() ?? "";
            var latest = tag.StartsWith("v") ? tag[1..] : tag;
            if (!IsNewer(latest, AppVersion)) return;

            // Find .zip asset
            string? dlUrl = null;
            foreach (var asset in doc.RootElement.GetProperty("assets").EnumerateArray())
            {
                var name = asset.GetProperty("name").GetString() ?? "";
                if (name.EndsWith(".zip"))
                {
                    dlUrl = asset.GetProperty("browser_download_url").GetString();
                    break;
                }
            }
            if (dlUrl == null) return;

            await DownloadAndPrompt(latest, dlUrl);
        }
        catch { }
    }

    private static bool IsNewer(string a, string b)
    {
        var av = a.Split('.').Select(s => int.TryParse(s, out var n) ? n : 0).ToArray();
        var bv = b.Split('.').Select(s => int.TryParse(s, out var n) ? n : 0).ToArray();
        for (int i = 0; i < Math.Max(av.Length, bv.Length); i++)
        {
            var ai = i < av.Length ? av[i] : 0;
            var bi = i < bv.Length ? bv[i] : 0;
            if (ai != bi) return ai > bi;
        }
        return false;
    }

    private async Task DownloadAndPrompt(string version, string url)
    {
        var dest = Path.Combine(Path.GetTempPath(), $"TokenTracker-{version}.zip");
        if (!File.Exists(dest))
        {
            var data = await _http.GetByteArrayAsync(url);
            await File.WriteAllBytesAsync(dest, data);
        }

        // Show prompt on the UI thread
        Application.OpenForms[0]?.Invoke(() => PromptInstall(version, dest));
    }

    private static void PromptInstall(string version, string zipPath)
    {
        var result = MessageBox.Show(
            $"Token Tracker {version} is ready.\nInstall now and restart?",
            "Update Available",
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Information);
        if (result == DialogResult.Yes) Install(version, zipPath);
    }

    private static void Install(string version, string zipPath)
    {
        try
        {
            var appDir  = AppContext.BaseDirectory;
            var extract = Path.Combine(Path.GetTempPath(), $"TokenTracker-{version}-extracted");
            if (Directory.Exists(extract)) Directory.Delete(extract, true);
            ZipFile.ExtractToDirectory(zipPath, extract);

            // Write a small bat that waits, copies, relaunches
            var bat = Path.Combine(Path.GetTempPath(), "tt_update.bat");
            var exe = Path.Combine(appDir, "TokenTracker.exe");
            File.WriteAllText(bat,
                $"@echo off\r\n" +
                $"timeout /t 2 /nobreak >nul\r\n" +
                $"xcopy /e /y /i \"{extract}\\*\" \"{appDir}\"\r\n" +
                $"start \"\" \"{exe}\"\r\n" +
                $"del \"%~f0\"\r\n");

            Process.Start(new ProcessStartInfo("cmd.exe", $"/c \"{bat}\"")
            {
                CreateNoWindow  = true,
                UseShellExecute = false
            });
            Application.Exit();
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Update failed: {ex.Message}\nPlease install manually.",
                "Update Error", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }
}
