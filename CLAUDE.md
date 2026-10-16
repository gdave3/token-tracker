# Token Tracker

macOS menu bar app that tracks Claude API token usage in real time.

## Architecture

Two implementations exist; the **Swift build is canonical** (`swift_build/`). The `build/` directory holds an older Python-only prototype using `rumps`.

### Swift build (`swift_build/`)

| File | Role |
|------|------|
| `main.swift` | Swift app: menu bar item, dashboard `WKWebView` window, spawns Python server |
| `server.py` | Python HTTP server (port 8765) + orchestrates `LiveWatcher` |
| `plugin_live.py` | `LiveWatcher` — tails JSONL and LevelDB files for new token events |
| `token-dashboard.html` | Dashboard frontend (gitignored, served by `server.py`) |

### Data flow

1. `main.swift` launches `server.py` via `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`
2. `server.py` imports `LiveWatcher` from `plugin_live.py` and calls `watcher.start()`
3. `LiveWatcher` watches two sources via `watchdog`:
   - **Claude Code JSONL** (`~/.claude/projects/**/*.jsonl`) — exact token counts from `usage` fields in `assistant` messages; also captures `compact_boundary` events for dropped tokens
   - **claude.ai LevelDB WAL** (`~/Library/Application Support/Claude/IndexedDB/https_claude.ai_0.indexeddb.leveldb/000039.log`) — estimated counts extracted from raw binary
4. Token events are appended to `~/Library/Application Support/TokenTracker/usage_log.json`
5. Dashboard fetches `/usage_log.json` from `http://localhost:8765`
6. Menu bar title refreshes every 10 seconds showing today's total

### Log file schema

```json
{
  "version": 1,
  "entries": [
    {
      "ts": "2026-10-16T12:00:00Z",
      "type": "live",
      "model": "claude-sonnet-4-6",
      "note": "claude-code:abcd1234",
      "input_tokens": 1234,
      "output_tokens": 567,
      "cost_usd": 0.008
    }
  ],
  "totals": { "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0 }
}
```

Entry `type` values: `live` (from watcher), `estimate` (clipboard paste), `compaction` (context compression boundary).

## Building & running

### Swift app (current)

```bash
cd swift_build
swiftc main.swift -framework Cocoa -framework WebKit -o "Token Tracker"
open TokenTracker.app   # or run the binary directly
```

Python 3.14 must be at `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`.

Required Python package: `watchdog` (`pip3 install watchdog`).

### Python standalone test

```bash
cd swift_build
python3 plugin_live.py   # prints new token events to stdout
```

## Pricing model (per million tokens)

| Model | Input | Output |
|-------|-------|--------|
| claude-opus-5-5 | $4.00 | $20.00 |
| claude-sonnet-5-5 | $2.00 | $10.00 |
| claude-haiku-5-5 | $0.10 | $0.50 |
| (default / unknown) | $2.00 | $10.00 |

## Key details

- Dashboard served at `http://localhost:8765`; `WKWebView` loads that URL
- `LiveWatcher.prime()` sets file positions to EOF on startup so only new data is captured
- LevelDB watcher reads the primary WAL file (`000039.log`) and may need updating if Claude rotates to a new log file
- `usage_log.json` is gitignored (personal data)
- `token-dashboard.html`, `plugin_live.py` inside `swift_build/` are gitignored (copied there from source during build)
