"""
Compaction plugin — detects and accounts for tokens dropped during autocompaction.

Two sources:
  1. Claude Code JSONL transcripts (exact data from compactMetadata)
  2. Pasted claude.ai conversation text (pattern detection + estimation)
"""

import re
import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class CompactionEvent:
    pre_tokens: int
    post_tokens: int
    dropped_tokens: int
    trigger: str  # "auto" | "manual"
    session_id: str = ""
    timestamp: str = ""


@dataclass
class CompactionReport:
    events: list = field(default_factory=list)
    total_dropped: int = 0
    source: str = ""   # "jsonl" | "text_pattern"

    def summary_line(self) -> str:
        if not self.events:
            return "No compaction detected."
        n = len(self.events)
        return (
            f"{n} compaction{'s' if n > 1 else ''} detected  |  "
            f"~{self.total_dropped:,} tokens dropped  ({self.source})"
        )


# ── Source 1: Claude Code JSONL ───────────────────────────────────────────────

CLAUDE_SESSIONS_DIR = Path.home() / ".claude" / "projects"


def scan_jsonl_for_compaction(jsonl_path: Path) -> CompactionReport:
    """Parse a Claude Code .jsonl transcript for exact compaction metadata."""
    report = CompactionReport(source="jsonl (exact)")
    try:
        for line in jsonl_path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("subtype") == "compact_boundary":
                meta = obj.get("compactMetadata", {})
                event = CompactionEvent(
                    pre_tokens=meta.get("preTokens", 0),
                    post_tokens=meta.get("postTokens", 0),
                    dropped_tokens=meta.get("cumulativeDroppedTokens", 0),
                    trigger=meta.get("trigger", "auto"),
                    session_id=obj.get("sessionId", ""),
                    timestamp=obj.get("timestamp", ""),
                )
                report.events.append(event)
                report.total_dropped += event.dropped_tokens
    except Exception:
        pass
    return report


def scan_all_sessions(session_dir: Path = CLAUDE_SESSIONS_DIR) -> dict:
    """Scan all Claude Code sessions, return {session_id: CompactionReport}."""
    results = {}
    for jsonl in session_dir.glob("**/*.jsonl"):
        report = scan_jsonl_for_compaction(jsonl)
        if report.events:
            sid = report.events[0].session_id or jsonl.stem
            results[sid] = report
    return results


def get_session_usage(jsonl_path: Path) -> dict:
    """Extract exact token usage from a Claude Code session transcript."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "dropped_tokens": 0,
        "compaction_count": 0,
    }
    try:
        for line in jsonl_path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Exact usage from API responses
            if obj.get("type") == "assistant":
                usage = obj.get("message", {}).get("usage", {})
                if usage:
                    totals["input_tokens"]  += usage.get("input_tokens", 0)
                    totals["output_tokens"] += usage.get("output_tokens", 0)
                    totals["cache_creation_input_tokens"] += usage.get("cache_creation_input_tokens", 0)
                    totals["cache_read_input_tokens"]     += usage.get("cache_read_input_tokens", 0)

            # Compaction events
            if obj.get("subtype") == "compact_boundary":
                meta = obj.get("compactMetadata", {})
                totals["dropped_tokens"]    += meta.get("cumulativeDroppedTokens", 0)
                totals["compaction_count"]  += 1

    except Exception:
        pass
    return totals


# ── Source 2: Pasted conversation text (claude.ai) ───────────────────────────

# Claude Code compaction header injected into pasted text
_CC_COMPACTION_RE = re.compile(
    r"This session is being continued from a previous conversation that ran out of context[.\s]+"
    r"The (?:summary|following)[^\n]*\n"
    r"([\s\S]*?)(?=\nHuman:|\nYou:|\nUser:|\nAssistant:|\nClaude:|\Z)",
    re.IGNORECASE,
)

# claude.ai UI markers (these appear as visual dividers in the copied text)
_CLAUDEAI_MARKERS = [
    re.compile(r"Context condensed", re.IGNORECASE),
    re.compile(r"Earlier context was condensed", re.IGNORECASE),
    re.compile(r"Context limit reached", re.IGNORECASE),
    re.compile(r"\[Conversation compacted\]", re.IGNORECASE),
    re.compile(r"context has been (summarized|compressed|condensed)", re.IGNORECASE),
]

# Typical compression ratio from real data: preTokens ~167K → postTokens ~8-14K
# Observed: 167119→8518 (19.6x), 167776→14381 (11.7x), 167939→9554 (17.6x), 167642→12105 (13.9x)
# Conservative middle: ~15x
_DEFAULT_COMPRESSION_RATIO = 15


def detect_compaction_in_text(text: str) -> CompactionReport:
    """
    Scan pasted conversation text for compaction markers.
    Returns a CompactionReport with estimated dropped tokens.
    """
    report = CompactionReport(source="text pattern (estimated ±30%)")

    # Claude Code compaction summaries
    for match in _CC_COMPACTION_RE.finditer(text):
        summary_text = match.group(1).strip()
        summary_tokens = _estimate_tokens(summary_text)
        # The summary represents ~15x its own size in original tokens
        estimated_original = summary_tokens * _DEFAULT_COMPRESSION_RATIO
        dropped_estimate = estimated_original - summary_tokens
        event = CompactionEvent(
            pre_tokens=estimated_original,
            post_tokens=summary_tokens,
            dropped_tokens=dropped_estimate,
            trigger="auto",
        )
        report.events.append(event)
        report.total_dropped += dropped_estimate

    # claude.ai visual markers (can't estimate size, just flag it)
    for marker_re in _CLAUDEAI_MARKERS:
        for _ in marker_re.finditer(text):
            event = CompactionEvent(
                pre_tokens=0,
                post_tokens=0,
                dropped_tokens=0,  # unknown without summary text
                trigger="auto",
            )
            report.events.append(event)
            # Flag it even without a size estimate

    return report


def _estimate_tokens(text: str) -> int:
    code_chars = sum(len(m) for m in re.findall(r'```[\s\S]*?```', text))
    rest_chars = len(text) - code_chars
    return int(code_chars / 3 + rest_chars / 4)


# ── Convenience: annotate a count result ─────────────────────────────────────

def adjust_for_compaction(input_tok: int, output_tok: int, text: str) -> tuple:
    """
    Given a base token estimate and raw conversation text,
    return (adjusted_input, adjusted_output, compaction_report).
    The dropped tokens are added back to input (they were context the model read).
    """
    report = detect_compaction_in_text(text)
    adjusted_input = input_tok + report.total_dropped
    return adjusted_input, output_tok, report


# ── CLI entry point (standalone) ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "sessions":
        # Scan all Claude Code sessions
        print("\n  Claude Code session compaction report\n")
        all_sessions = scan_all_sessions()
        if not all_sessions:
            print("  No compaction events found in any session.")
        else:
            total_dropped = sum(r.total_dropped for r in all_sessions.values())
            for sid, report in sorted(all_sessions.items()):
                print(f"  Session: {sid[:12]}…")
                for e in report.events:
                    print(f"    {e.timestamp[:16]}  {e.pre_tokens:,} → {e.post_tokens:,}  "
                          f"dropped: {e.dropped_tokens:,}  [{e.trigger}]")
            print(f"\n  Total tokens dropped across all sessions: {total_dropped:,}")
    else:
        # Read from stdin
        print("Paste conversation text, then Ctrl-D:")
        try:
            text = sys.stdin.read()
        except KeyboardInterrupt:
            sys.exit(0)
        report = detect_compaction_in_text(text)
        print(f"\n  {report.summary_line()}")
        for i, e in enumerate(report.events, 1):
            if e.dropped_tokens:
                print(f"  Event {i}: ~{e.pre_tokens:,} → ~{e.post_tokens:,}  "
                      f"(~{e.dropped_tokens:,} dropped)")
            else:
                print(f"  Event {i}: compaction marker detected (size unknown)")
