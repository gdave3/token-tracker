#!/usr/bin/env python3
"""
Claude Token Tracker — macOS menu bar app.

Usage:
  1. Finish a Claude conversation
  2. Select all text on the page (Cmd+A) and copy (Cmd+C)
  3. Click the menu bar icon → "Log from clipboard"
  4. Done — tokens estimated and saved automatically.
"""

import re
import json
import rumps
from datetime import datetime, date
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
LOG_FILE = Path(__file__).parent / "usage_log.json"
DEFAULT_MODEL = "claude-sonnet-5-5"   # subscription default

MODELS = {
    "claude-opus-5-5":   {"input": 4.00,  "output": 20.00},
    "claude-sonnet-5-5": {"input": 2.00,  "output": 10.00},
    "claude-haiku-5-5":  {"input": 0.10,  "output": 0.50},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    code_chars = sum(len(m) for m in re.findall(r'```[\s\S]*?```', text))
    rest_chars = len(text) - code_chars
    return int(code_chars / 3 + rest_chars / 4)


def split_turns(text: str):
    """Return (input_tokens, output_tokens) by splitting Human/Claude turns."""
    turn_re = re.compile(r'\n?(?:Human|You|User|Assistant|Claude)\s*:\s*', re.IGNORECASE)
    parts = turn_re.split(text)
    headers = turn_re.findall(text)
    if len(parts) <= 2:
        total = estimate_tokens(text)
        return total, 0
    inp = out = 0
    for i, part in enumerate(parts[1:], 0):
        h = headers[i].strip().rstrip(':').lower() if i < len(headers) else ''
        tok = estimate_tokens(part)
        if h in ('human', 'you', 'user'):
            inp += tok
        else:
            out += tok
    return inp, out


def cost_usd(inp: int, out: int, model: str) -> float:
    p = MODELS.get(model, MODELS[DEFAULT_MODEL])
    return (inp * p["input"] + out * p["output"]) / 1_000_000


def load_log() -> dict:
    if LOG_FILE.exists():
        return json.loads(LOG_FILE.read_text())
    return {"version": 1, "entries": [], "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}


def save_log(data: dict):
    LOG_FILE.write_text(json.dumps(data, indent=2))


def today_stats(data: dict) -> tuple:
    today = date.today().isoformat()
    tok = cost = 0.0
    for e in data["entries"]:
        if (e.get("ts") or "").startswith(today):
            tok += e.get("input_tokens", 0) + e.get("output_tokens", 0)
            cost += e.get("cost_usd", 0)
    return int(tok), cost


def fmt_cost(c: float) -> str:
    if c == 0:
        return "$0.00"
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:.2f}"


def fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def get_clipboard() -> str:
    try:
        import subprocess
        result = subprocess.run(["pbpaste"], capture_output=True, text=True)
        return result.stdout
    except Exception:
        return ""


# ── App ───────────────────────────────────────────────────────────────────────

class TokenTrackerApp(rumps.App):
    def __init__(self):
        data = load_log()
        tok, cost = today_stats(data)
        title = self._make_title(tok, cost)
        super().__init__(title, quit_button=None)
        self.model = DEFAULT_MODEL
        self._build_menu()

    def _make_title(self, tok: int, cost: float) -> str:
        if tok == 0:
            return "𝒯 —"
        return f"𝒯 {fmt_tok(tok)}"

    def _build_menu(self):
        data = load_log()
        tok, cost = today_stats(data)
        t = data["totals"]

        self.menu.clear()
        self.menu = [
            rumps.MenuItem("📋  Log from clipboard", callback=self.log_clipboard),
            None,
            rumps.MenuItem(f"Today:  {fmt_tok(tok)} tokens  ({fmt_cost(cost)})", callback=None),
            rumps.MenuItem(f"Total:  {fmt_tok(t['input_tokens'] + t['output_tokens'])} tokens  ({fmt_cost(t['cost_usd'])})", callback=None),
            rumps.MenuItem(f"Entries: {len(data['entries'])}", callback=None),
            None,
            self._model_menu(),
            None,
            rumps.MenuItem("📂  Open log file", callback=self.open_log),
            rumps.MenuItem("🔄  Refresh", callback=self.refresh),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

    def _model_menu(self):
        item = rumps.MenuItem("Model")
        for mid in MODELS:
            label = mid + (" ✓" if mid == self.model else "")
            sub = rumps.MenuItem(label, callback=self.set_model)
            sub._model_id = mid
            item[label] = sub
        return item

    @rumps.clicked("🔄  Refresh")
    def refresh(self, _=None):
        data = load_log()
        tok, cost = today_stats(data)
        self.title = self._make_title(tok, cost)
        self._build_menu()

    def set_model(self, sender):
        self.model = sender._model_id
        self._build_menu()

    @rumps.clicked("📋  Log from clipboard")
    def log_clipboard(self, _=None):
        text = get_clipboard()
        if not text or len(text) < 20:
            rumps.notification(
                title="Claude Token Tracker",
                subtitle="Nothing to log",
                message="Copy a conversation first (Cmd+A then Cmd+C on claude.ai).",
            )
            return

        inp, out = split_turns(text)
        total = inp + out
        if total == 0:
            rumps.notification(
                title="Claude Token Tracker",
                subtitle="Nothing to log",
                message="Clipboard doesn't look like a conversation.",
            )
            return

        # Compaction detection — add back compressed tokens
        compaction_note = ""
        try:
            from plugin_compaction import adjust_for_compaction
            inp, out, comp_report = adjust_for_compaction(inp, out, text)
            total = inp + out
            if comp_report.events and comp_report.total_dropped > 0:
                compaction_note = f" (+{fmt_tok(comp_report.total_dropped)} compacted)"
        except ImportError:
            pass

        c = cost_usd(inp, out, self.model)
        data = load_log()
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "type": "estimate",
            "model": self.model,
            "note": f"clipboard{compaction_note}",
            "input_tokens": inp,
            "output_tokens": out,
            "cost_usd": c,
        }
        data["entries"].append(entry)
        data["totals"]["input_tokens"] += inp
        data["totals"]["output_tokens"] += out
        data["totals"]["cost_usd"] = round(data["totals"]["cost_usd"] + c, 8)
        save_log(data)

        self.refresh()
        rumps.notification(
            title="Claude Token Tracker",
            subtitle=f"Logged ~{fmt_tok(total)} tokens{compaction_note}",
            message=f"In: {fmt_tok(inp)}  Out: {fmt_tok(out)}  Est. cost: {fmt_cost(c)}",
        )

    @rumps.clicked("📂  Open log file")
    def open_log(self, _=None):
        import subprocess
        if LOG_FILE.exists():
            subprocess.run(["open", str(LOG_FILE)])
        else:
            rumps.notification(
                title="Claude Token Tracker",
                subtitle="No log yet",
                message="Log a conversation first.",
            )


if __name__ == "__main__":
    TokenTrackerApp().run()
