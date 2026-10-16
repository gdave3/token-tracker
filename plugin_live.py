"""
Live watcher plugin — auto-detects new Claude messages and updates the menu bar.

Two sources watched simultaneously:
  • Claude Code JSONL  (~/.claude/projects/**/*.jsonl)
      → exact token counts from API usage fields
  • claude.ai LevelDB  (~/Library/Application Support/Claude/IndexedDB/…)
      → new text content extracted from .log file as it grows
"""

import re
import json
import sys
import time
import threading
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── Paths ─────────────────────────────────────────────────────────────────────

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CLAUDE_LEVELDB  = (
    Path.home()
    / "Library/Application Support/Claude/IndexedDB"
    / "https_claude.ai_0.indexeddb.leveldb"
)
LEVELDB_LOG = CLAUDE_LEVELDB / "000039.log"   # primary write-ahead log


# ── Helpers ───────────────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    code = sum(len(m) for m in re.findall(r'```[\s\S]*?```', text))
    rest = len(text) - code
    return int(code / 3 + rest / 4)


def _extract_strings(raw_bytes: bytes, min_len: int = 12) -> list:
    """Pull printable ASCII strings out of raw binary bytes."""
    pattern = rb'[ -~]{' + str(min_len).encode() + rb',}'
    return [m.decode("ascii", errors="ignore") for m in re.findall(pattern, raw_bytes)]


# ── JSONL watcher (Claude Code — exact counts) ────────────────────────────────

class JsonlWatcher:
    """
    Tails Claude Code .jsonl session files for new assistant messages.
    Calls on_tokens(input_tok, output_tok, model, source) for each new entry.
    """

    def __init__(self, on_tokens):
        self.on_tokens = on_tokens
        self._positions: dict = {}   # path → byte offset
        self._lock = threading.Lock()

    def _scan_file(self, path: Path):
        try:
            with self._lock:
                pos = self._positions.get(str(path), 0)
            with open(path, "rb") as f:
                f.seek(pos)
                new_bytes = f.read()
                new_pos = f.tell()
            if not new_bytes:
                return
            with self._lock:
                self._positions[str(path)] = new_pos
            for line in new_bytes.decode("utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "assistant":
                    usage = obj.get("message", {}).get("usage", {})
                    if usage.get("output_tokens", 0) > 0:
                        inp = (
                            usage.get("input_tokens", 0)
                            + usage.get("cache_creation_input_tokens", 0)
                            + usage.get("cache_read_input_tokens", 0)
                        )
                        out   = usage.get("output_tokens", 0)
                        model = obj.get("message", {}).get("model", "claude-sonnet-4-6")
                        self.on_tokens(inp, out, model, f"claude-code:{path.stem[:8]}")
                if obj.get("subtype") == "compact_boundary":
                    dropped = obj.get("compactMetadata", {}).get("cumulativeDroppedTokens", 0)
                    if dropped:
                        self.on_tokens(dropped, 0, "unknown", "compaction")
        except Exception:
            pass

    def prime(self):
        """Set initial file positions to end-of-file so we only watch new data."""
        for jsonl in CLAUDE_PROJECTS.glob("**/*.jsonl"):
            try:
                self._positions[str(jsonl)] = jsonl.stat().st_size
            except Exception:
                pass

    def on_modified(self, path: Path):
        if path.suffix == ".jsonl":
            self._scan_file(path)

    def on_created(self, path: Path):
        if path.suffix == ".jsonl":
            with self._lock:
                self._positions[str(path)] = 0
            self._scan_file(path)


# ── LevelDB log watcher (claude.ai — estimated counts) ───────────────────────

class LevelDBWatcher:
    """
    Tails the claude.ai IndexedDB write-ahead log for new message content.
    Calls on_tokens with an estimated count when new text is detected.
    """

    def __init__(self, on_tokens):
        self.on_tokens = on_tokens
        self._position = 0
        self._last_text = ""

    def prime(self):
        if LEVELDB_LOG.exists():
            self._position = LEVELDB_LOG.stat().st_size

    def on_modified(self, path: Path):
        if path.name not in ("000039.log",) and not path.name.endswith(".log"):
            return
        # Only process the primary log file
        if path.parent != CLAUDE_LEVELDB:
            return
        try:
            with open(path, "rb") as f:
                f.seek(self._position)
                new_bytes = f.read()
                self._position = f.tell()
            if len(new_bytes) < 20:
                return
            strings = _extract_strings(new_bytes)
            # Filter to conversation-like content
            content_chunks = [
                s for s in strings
                if not s.startswith("{") and not s.startswith("http")
                and len(s) > 20
                and not re.match(r'^[a-f0-9\-]{30,}$', s)   # skip UUIDs
                and not re.match(r'^\d{4}-\d{2}-\d{2}', s)  # skip timestamps
            ]
            if not content_chunks:
                return
            new_text = " ".join(content_chunks)
            if new_text == self._last_text:
                return
            self._last_text = new_text
            tok = _estimate_tokens(new_text)
            if tok > 5:
                self.on_tokens(tok, 0, "claude-subscription", "claude.ai")
        except Exception:
            pass


# ── Unified FSEvents handler ──────────────────────────────────────────────────

class _Handler(FileSystemEventHandler):
    def __init__(self, jsonl_watcher: JsonlWatcher, leveldb_watcher: LevelDBWatcher):
        self._j = jsonl_watcher
        self._l = leveldb_watcher

    def on_modified(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        self._j.on_modified(p)
        self._l.on_modified(p)

    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        self._j.on_created(p)


# ── Public API ────────────────────────────────────────────────────────────────

class LiveWatcher:
    """
    Start with LiveWatcher(on_tokens=callback).start()
    callback signature: on_tokens(input_tok, output_tok, model, source)
    """

    def __init__(self, on_tokens):
        self._on_tokens = on_tokens
        self._jsonl  = JsonlWatcher(on_tokens)
        self._leveldb = LevelDBWatcher(on_tokens)
        self._observer = Observer()

    def start(self):
        # Prime — set file positions to EOF so we only catch new data
        self._jsonl.prime()
        self._leveldb.prime()

        handler = _Handler(self._jsonl, self._leveldb)

        # Watch Claude Code projects directory
        if CLAUDE_PROJECTS.exists():
            self._observer.schedule(handler, str(CLAUDE_PROJECTS), recursive=True)

        # Watch claude.ai LevelDB directory
        if CLAUDE_LEVELDB.exists():
            self._observer.schedule(handler, str(CLAUDE_LEVELDB), recursive=False)

        self._observer.start()

    def stop(self):
        self._observer.stop()
        self._observer.join()


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Watching for new Claude messages… (Ctrl-C to stop)\n")

    def on_new(inp, out, model, source):
        total = inp + out
        print(f"  [{source}]  model={model}  in={inp:,}  out={out:,}  total={total:,}")

    watcher = LiveWatcher(on_tokens=on_new)
    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
        print("\nStopped.")
