"""The leader chat thread, `runs/leader.chat.jsonl`: one JSON object per line,
append-only, the operator and the chair lock's holder trading messages
through a file each side polls."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

__all__ = ["CHAT_FILENAME", "append_line", "chat_path", "read_thread", "unread"]

CHAT_FILENAME = "leader.chat.jsonl"


def chat_path(runs_dir: Path) -> Path:
    return Path(runs_dir) / CHAT_FILENAME


def append_line(existing: str, entry: Mapping) -> str:
    return existing + json.dumps(dict(entry)) + "\n"


def read_thread(text: str, limit: int) -> list[dict]:
    """Skips any line that is not a parseable JSON object, never raising."""
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries[max(len(entries) - limit, 0):]


def unread(thread: Sequence[Mapping], since: str | None) -> list[dict]:
    if since is None:
        return list(thread)
    return [entry for entry in thread if str(entry.get("at", "")) > since]
