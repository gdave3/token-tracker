#!/usr/bin/env python3
"""Claude token usage tracker — precise via API, or local estimation from conversation text."""

import sys
import json
import re
import argparse
from datetime import datetime
from pathlib import Path

# ── Pricing table ($/1M tokens, as of 2026-10) ────────────────────────────────
MODELS = {
    "claude-opus-5-5":   {"input": 4.00,  "output": 20.00, "label": "Claude Opus 5.5"},
    "claude-sonnet-5-5": {"input": 2.00,  "output": 10.00, "label": "Claude Sonnet 5.5"},
    "claude-haiku-5-5":  {"input": 0.10,  "output": 0.50,  "label": "Claude Haiku 5.5"},
    "claude-fable-5-1":  {"input": 10.00, "output": 50.00, "label": "Claude Fable 5.1"},
    "claude-opus-5":     {"input": 5.00,  "output": 25.00, "label": "Claude Opus 5"},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "label": "Claude Sonnet 4.6"},
    "claude-haiku-4-5":  {"input": 1.00,  "output": 5.00,  "label": "Claude Haiku 4.5"},
}
DEFAULT_MODEL = "claude-opus-5-5"

LOG_FILE = Path(__file__).parent / "usage_log.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_client():
    try:
        from anthropic import Anthropic
        return Anthropic()
    except ImportError:
        sys.exit("Error: anthropic package not installed. Run: pip install anthropic")


def load_log() -> dict:
    if LOG_FILE.exists():
        return json.loads(LOG_FILE.read_text())
    return {
        "version": 1,
        "entries": [],
        "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
    }


def save_log(data: dict):
    LOG_FILE.write_text(json.dumps(data, indent=2))


def cost_usd(input_tok: int, output_tok: int, model: str) -> float:
    p = MODELS.get(model, MODELS[DEFAULT_MODEL])
    return (input_tok * p["input"] + output_tok * p["output"]) / 1_000_000


def fmt_tokens(n: int) -> str:
    return f"{n:,}"


def fmt_cost(c: float) -> str:
    if c < 0.01:
        return f"${c:.6f}"
    return f"${c:.4f}"


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_count(args):
    """Count input tokens for a piece of text (no message is sent to Claude)."""
    client = get_client()

    if args.file:
        text = Path(args.file).read_text()
        source = f"file: {args.file}"
    else:
        text = args.text
        source = "text"

    messages = [{"role": "user", "content": text}]
    kwargs = {"model": args.model, "messages": messages}
    if args.system:
        kwargs["system"] = args.system

    resp = client.messages.count_tokens(**kwargs)
    tok = resp.input_tokens
    p = MODELS.get(args.model, MODELS[DEFAULT_MODEL])
    est_cost = tok * p["input"] / 1_000_000

    print(f"  source : {source}")
    print(f"  model  : {args.model}")
    print(f"  tokens : {fmt_tokens(tok)}")
    print(f"  cost   : {fmt_cost(est_cost)}  (input-side only, at ${p['input']}/1M)")

    if args.log:
        data = load_log()
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "type": "count",
            "model": args.model,
            "source": source,
            "input_tokens": tok,
            "output_tokens": 0,
            "cost_usd": est_cost,
        }
        data["entries"].append(entry)
        data["totals"]["input_tokens"] += tok
        data["totals"]["cost_usd"] = round(data["totals"]["cost_usd"] + est_cost, 8)
        save_log(data)
        print(f"  logged ✓")


def cmd_log(args):
    """Log a completed exchange: supply actual input & output token counts."""
    model = args.model
    p = MODELS.get(model, MODELS[DEFAULT_MODEL])
    c = cost_usd(args.input_tokens, args.output_tokens, model)

    print(f"  model         : {model}")
    print(f"  input tokens  : {fmt_tokens(args.input_tokens)}")
    print(f"  output tokens : {fmt_tokens(args.output_tokens)}")
    print(f"  cost          : {fmt_cost(c)}")

    data = load_log()
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "type": "exchange",
        "model": model,
        "note": args.note or "",
        "input_tokens": args.input_tokens,
        "output_tokens": args.output_tokens,
        "cost_usd": c,
    }
    data["entries"].append(entry)
    data["totals"]["input_tokens"] += args.input_tokens
    data["totals"]["output_tokens"] += args.output_tokens
    data["totals"]["cost_usd"] = round(data["totals"]["cost_usd"] + c, 8)
    save_log(data)
    print("  logged ✓")


def cmd_stats(args):
    """Print a usage summary from the log."""
    data = load_log()
    entries = data["entries"]

    if not entries:
        print("No entries logged yet.")
        print(f"  Log file: {LOG_FILE}")
        return

    t = data["totals"]
    print(f"\n{'─'*46}")
    print(f"  Logged entries  : {len(entries)}")
    print(f"  Total input tok : {fmt_tokens(t['input_tokens'])}")
    print(f"  Total output tok: {fmt_tokens(t['output_tokens'])}")
    total_tok = t["input_tokens"] + t["output_tokens"]
    print(f"  Total tokens    : {fmt_tokens(total_tok)}")
    print(f"  Total cost      : {fmt_cost(t['cost_usd'])}")
    print(f"{'─'*46}")

    # Per-model breakdown
    from collections import defaultdict
    by_model: dict = defaultdict(lambda: {"input": 0, "output": 0, "cost": 0.0, "n": 0})
    for e in entries:
        m = e.get("model", "unknown")
        by_model[m]["input"] += e.get("input_tokens", 0)
        by_model[m]["output"] += e.get("output_tokens", 0)
        by_model[m]["cost"] += e.get("cost_usd", 0.0)
        by_model[m]["n"] += 1

    print("\n  By model:")
    for m, v in sorted(by_model.items(), key=lambda x: -x[1]["cost"]):
        label = MODELS.get(m, {}).get("label", m)
        print(f"    {label}")
        print(f"      {v['n']} entries  |  in {fmt_tokens(v['input'])}  out {fmt_tokens(v['output'])}  |  {fmt_cost(v['cost'])}")

    # Last 5 entries
    print("\n  Recent entries:")
    for e in reversed(entries[-5:]):
        ts = e["ts"][:16].replace("T", " ")
        note = f"  [{e['note']}]" if e.get("note") else ""
        print(f"    {ts}  {e['model']}  in:{fmt_tokens(e['input_tokens'])}  out:{fmt_tokens(e['output_tokens'])}  {fmt_cost(e['cost_usd'])}{note}")

    print(f"\n  Log file: {LOG_FILE}")


def cmd_prices(args):
    """Print the current pricing table."""
    print(f"\n  {'Model':<22}  {'Input $/1M':>11}  {'Output $/1M':>12}")
    print(f"  {'─'*22}  {'─'*11}  {'─'*12}")
    for mid, p in MODELS.items():
        marker = " ◀ default" if mid == DEFAULT_MODEL else ""
        print(f"  {mid:<22}  ${p['input']:>10.2f}  ${p['output']:>11.2f}{marker}")


def cmd_calc(args):
    """Calculate cost for given token counts without logging."""
    for model in (args.models or [DEFAULT_MODEL]):
        p = MODELS.get(model, MODELS[DEFAULT_MODEL])
        c = cost_usd(args.input_tokens, args.output_tokens, model)
        print(f"  {model:<22}  {fmt_cost(c)}")


def estimate_tokens(text: str) -> int:
    """Local token estimate: chars/4 with adjustments for code and whitespace."""
    code_chars = sum(len(m) for m in re.findall(r'```[\s\S]*?```', text))
    rest_chars = len(text) - code_chars
    return int(code_chars / 3 + rest_chars / 4)


def _load_compaction():
    try:
        from plugin_compaction import adjust_for_compaction
        return adjust_for_compaction
    except ImportError:
        return None


def cmd_estimate(args):
    """Estimate tokens locally from conversation text — no API key needed."""
    if args.file:
        raw = Path(args.file).read_text()
    elif args.text:
        raw = args.text
    else:
        print("Paste your conversation text, then press Enter + Ctrl-D (Mac: Ctrl-D on a new line):")
        try:
            raw = sys.stdin.read()
        except KeyboardInterrupt:
            print("\nCancelled.")
            return

    # Try to split Human / Assistant turns
    # Handles "Human:", "You:", "User:", "Assistant:", "Claude:"
    turn_re = re.compile(r'\n?(?:Human|You|User|Assistant|Claude)\s*:\s*', re.IGNORECASE)
    parts = turn_re.split(raw)
    headers = turn_re.findall(raw)

    if len(parts) > 2:
        input_tok = output_tok = 0
        for i, part in enumerate(parts[1:], 0):
            h = headers[i].strip().rstrip(':').lower() if i < len(headers) else ''
            tok = estimate_tokens(part)
            if h in ('human', 'you', 'user'):
                input_tok += tok
            else:
                output_tok += tok
        total = input_tok + output_tok
        print(f"\n  ── Local estimate (±10-15%) ──────────────────")
        print(f"  Input  (your messages) : ~{fmt_tokens(input_tok)}")
        print(f"  Output (Claude replies): ~{fmt_tokens(output_tok)}")
        print(f"  Total                  : ~{fmt_tokens(total)}")
    else:
        total = estimate_tokens(raw)
        input_tok = total
        output_tok = 0
        print(f"\n  ── Local estimate (±10-15%) ──────────────────")
        print(f"  Total tokens: ~{fmt_tokens(total)}")

    # Cost across selected models
    print(f"\n  Estimated cost if on API:")
    models = args.models if args.models else [args.model]
    for m in models:
        p = MODELS.get(m, MODELS[DEFAULT_MODEL])
        c = cost_usd(input_tok, output_tok, m)
        print(f"    {m:<22}  {fmt_cost(c)}")

    # Compaction detection
    adjust = _load_compaction()
    if adjust and raw:
        adj_input, adj_output, comp_report = adjust(input_tok, output_tok, raw)
        if comp_report.events:
            print(f"\n  ── Compaction detected ───────────────────────")
            print(f"  {comp_report.summary_line()}")
            print(f"  Adjusted input : ~{fmt_tokens(adj_input)}  (was {fmt_tokens(input_tok)})")
            input_tok = adj_input
            output_tok = adj_output
            total = input_tok + output_tok

    if args.log and total > 0:
        data = load_log()
        c = cost_usd(input_tok, output_tok, args.model)
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "type": "estimate",
            "model": args.model,
            "note": args.note or "local estimate",
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "cost_usd": c,
        }
        data["entries"].append(entry)
        data["totals"]["input_tokens"] += input_tok
        data["totals"]["output_tokens"] += output_tok
        data["totals"]["cost_usd"] = round(data["totals"]["cost_usd"] + c, 8)
        save_log(data)
        print(f"\n  logged ✓  ({fmt_cost(c)} at {args.model})")


def cmd_clear(args):
    """Delete the log file (irreversible)."""
    if not LOG_FILE.exists():
        print("No log file found.")
        return
    confirm = input(f"Delete {LOG_FILE}? [y/N] ").strip().lower()
    if confirm == "y":
        LOG_FILE.unlink()
        print("Log deleted.")
    else:
        print("Cancelled.")


# ── CLI wiring ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="tracker",
        description="Precise Claude token usage tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
commands:
  estimate  Estimate tokens locally from text/file/stdin — no API key needed
  count     Count tokens precisely via API (needs ANTHROPIC_API_KEY)
  log       Record an exchange with known token counts
  stats     Show usage summary
  prices    Show current model pricing
  calc      Calculate cost without logging
  clear     Delete the log file
""",
    )
    sub = parser.add_subparsers(dest="cmd")

    # count
    p_count = sub.add_parser("count", help="Count input tokens for text or a file")
    p_count.add_argument("text", nargs="?", help="Text to count")
    p_count.add_argument("-f", "--file", help="Count tokens in a file instead")
    p_count.add_argument("-m", "--model", default=DEFAULT_MODEL, choices=list(MODELS))
    p_count.add_argument("-s", "--system", help="Optional system prompt to include")
    p_count.add_argument("--log", action="store_true", help="Also append entry to the log")

    # log
    p_log = sub.add_parser("log", help="Record a completed exchange")
    p_log.add_argument("input_tokens", type=int, help="Input token count (from API response)")
    p_log.add_argument("output_tokens", type=int, help="Output token count")
    p_log.add_argument("-m", "--model", default=DEFAULT_MODEL, choices=list(MODELS))
    p_log.add_argument("-n", "--note", help="Optional note/label for the entry")

    # stats
    sub.add_parser("stats", help="Show usage summary from log")

    # prices
    sub.add_parser("prices", help="Show current pricing table")

    # calc
    p_calc = sub.add_parser("calc", help="Calculate cost (no logging)")
    p_calc.add_argument("input_tokens", type=int)
    p_calc.add_argument("output_tokens", type=int)
    p_calc.add_argument("-m", "--models", nargs="+", choices=list(MODELS),
                        help="Models to compare (default: all)")

    # estimate
    p_est = sub.add_parser("estimate", help="Estimate tokens locally — no API key needed")
    p_est.add_argument("text", nargs="?", help="Text to estimate (omit to read from stdin)")
    p_est.add_argument("-f", "--file", help="Estimate tokens in a file")
    p_est.add_argument("-m", "--model", default=DEFAULT_MODEL, choices=list(MODELS),
                       help="Model for cost calculation")
    p_est.add_argument("--models", nargs="+", choices=list(MODELS),
                       help="Compare cost across multiple models")
    p_est.add_argument("--log", action="store_true", help="Save estimate to log")
    p_est.add_argument("-n", "--note", help="Optional note")

    # clear
    sub.add_parser("clear", help="Delete the usage log")

    args = parser.parse_args()

    dispatch = {
        "count":    cmd_count,
        "log":      cmd_log,
        "estimate": cmd_estimate,
        "stats":    cmd_stats,
        "prices":   cmd_prices,
        "calc":     cmd_calc,
        "clear":    cmd_clear,
    }

    if args.cmd not in dispatch:
        parser.print_help()
        return

    # calc: default to all models when none specified
    if args.cmd == "calc" and not args.models:
        args.models = list(MODELS.keys())

    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
