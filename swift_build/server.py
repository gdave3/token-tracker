"""HTTP server + live file watcher. Started by the Swift app."""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import json, re, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from pathlib import Path

LOG_FILE       = Path.home() / "Library" / "Application Support" / "TokenTracker" / "usage_log.json"
DASHBOARD_FILE = Path(__file__).parent / "token-dashboard.html"
PORT           = 8765

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# ── HTTP server ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/usage_log.json":
            data = LOG_FILE.read_bytes() if LOG_FILE.exists() else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        elif self.path in ("/", "/index.html"):
            if DASHBOARD_FILE.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(DASHBOARD_FILE.read_bytes())
            else:
                self.send_response(404); self.end_headers()
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass

def start_server():
    try:
        HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except Exception as e:
        pass

threading.Thread(target=start_server, daemon=True).start()

# ── Live watcher ──────────────────────────────────────────────────────────────
def load_log():
    if LOG_FILE.exists():
        return json.loads(LOG_FILE.read_text())
    return {"version": 1, "entries": [], "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}

def save_log(data):
    LOG_FILE.write_text(json.dumps(data, indent=2))

def cost_usd(inp, out, model="claude-sonnet-5-5", cache_create=0, cache_read=0):
    # Per-million rates: (input, output, cache_write, cache_read)
    RATES = {
        "claude-opus-5-5":   (15.0, 75.0,  18.75, 1.50),
        "claude-sonnet-5-5": ( 3.0, 15.0,   3.75, 0.30),
        "claude-haiku-5-5":  ( 0.80, 4.0,   1.00, 0.08),
        "claude-opus-4-5":   (15.0, 75.0,  18.75, 1.50),
        "claude-sonnet-4-6": ( 3.0, 15.0,   3.75, 0.30),
        "claude-haiku-4-5":  ( 0.80, 4.0,   1.00, 0.08),
    }
    ri, ro, rcc, rcr = RATES.get(model, (3.0, 15.0, 3.75, 0.30))
    return (inp * ri + out * ro + cache_create * rcc + cache_read * rcr) / 1_000_000

def on_tokens(inp, out, model, source, cache_create=0, cache_read=0):
    if inp + out + cache_create + cache_read == 0: return
    c = cost_usd(inp, out, model, cache_create=cache_create, cache_read=cache_read)
    compute = inp + cache_create + out   # excludes cache reads
    data = load_log()
    data["entries"].append({
        "ts": datetime.utcnow().isoformat() + "Z",
        "type": "live", "model": model, "note": source,
        "input_tokens": inp, "cache_creation_tokens": cache_create,
        "cache_read_tokens": cache_read, "output_tokens": out,
        "compute_tokens": compute, "cost_usd": c,
    })
    data["totals"]["input_tokens"]  += inp
    data["totals"]["output_tokens"] += out
    data["totals"]["cost_usd"] = round(data["totals"]["cost_usd"] + c, 8)
    save_log(data)

try:
    from plugin_live import LiveWatcher, load_history

    def _bootstrap_history():
        """Load all historical JSONL data once on startup, skipping duplicates."""
        data = load_log()
        existing_notes = {e.get("note", "") + e.get("ts", "") for e in data.get("entries", [])}

        new_entries = []
        def on_hist(ts, inp, out, model, source, cache_create=0, cache_read=0):
            key = source + ts
            if key in existing_notes:
                return
            c = cost_usd(inp, out, model, cache_create=cache_create, cache_read=cache_read)
            compute = inp + cache_create + out
            new_entries.append({
                "ts": ts,
                "type": "history", "model": model, "note": source,
                "input_tokens": inp, "cache_creation_tokens": cache_create,
                "cache_read_tokens": cache_read, "output_tokens": out,
                "compute_tokens": compute, "cost_usd": c,
            })

        load_history(on_hist)

        if new_entries:
            new_entries.sort(key=lambda e: e["ts"])
            data["entries"] = new_entries + data["entries"]
            for e in new_entries:
                data["totals"]["input_tokens"]  += e["input_tokens"]
                data["totals"]["output_tokens"] += e["output_tokens"]
                data["totals"]["cost_usd"] = round(data["totals"]["cost_usd"] + e["cost_usd"], 8)
            save_log(data)

    _bootstrap_history()

    watcher = LiveWatcher(on_tokens=on_tokens)
    watcher.start()
except Exception:
    pass

# Keep process alive
import time
while True:
    time.sleep(60)
