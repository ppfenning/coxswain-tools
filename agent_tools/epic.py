"""Watch a detached harness run without spending a turn on it. Blocks in Python, returns facts."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

__all__ = ["alive", "summarize_log", "watch"]

_LINE = re.compile(r"^\s*(quarantined task|quarantined phase|reused|epic |  usage)", re.M)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def summarize_log(text: str) -> dict[str, Any]:
    """Pure: the lines of a run log that state an outcome."""
    lines = [l.strip() for l in text.splitlines() if _LINE.match(l)]
    return {
        "quarantined": [l for l in lines if l.startswith("quarantined")],
        "reused": [l for l in lines if l.startswith("reused")],
        "summary": next((l for l in lines if l.startswith("epic ")), None),
        "usage": next((l for l in lines if l.startswith("usage")), None),
    }


def watch(pidfile: Path | str, *, log: Path | str | None = None, max_seconds: float = 570, interval: float = 20) -> dict[str, Any]:
    """Wait for the pid in `pidfile` to exit, up to the cap. Returns whether it finished and the log's outcome lines."""
    pid = int(Path(pidfile).read_text().strip())
    deadline = time.monotonic() + max_seconds
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(interval)
    finished = not alive(pid)
    out: dict[str, Any] = {"pid": pid, "finished": finished}
    if log and Path(log).exists():
        out.update(summarize_log(Path(log).read_text(encoding="utf-8", errors="replace")))
    return out
