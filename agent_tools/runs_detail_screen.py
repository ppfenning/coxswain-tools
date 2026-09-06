"""Curses edge for `cox runs detail`: gathers what `runs_detail.detail` takes and draws the result."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from agent_tools import runs_top_screen
from agent_tools.records import load_trace

__all__ = ["draw", "facts_for"]


def _record(root: Path, run: str) -> dict:
    """Missing, torn or unreadable reads as `{}`, never a raise."""
    matches = sorted((root / run / "tasks").glob("*/*.json"))
    if not matches:
        return {}
    try:
        return json.loads(matches[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _content(event: dict) -> list:
    """A trace line's tool-use items, or none: `message` is a string on some lines and a mapping on others."""
    message = event.get("message") if isinstance(event, dict) else None
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    return content if isinstance(content, list) else []


def _tool_names(trace_events: list[dict]) -> list[str]:
    return [
        item["name"]
        for event in trace_events
        for item in _content(event)
        if isinstance(item, dict) and item.get("type") == "tool_use"
    ]


def _tail(root: Path, run: str) -> list[str]:
    trace_dir = root / f"{run}-trace"
    paths = [p for p in trace_dir.glob("*.jsonl") if runs_top_screen._TRACE_NAME.match(p.stem)] if trace_dir.exists() else []
    if not paths:
        return []
    newest = max(paths, key=lambda p: p.stat().st_mtime)
    return _tool_names(load_trace(newest))[-3:]


def facts_for(runs_dir, run: str, now_alive=runs_top_screen._default_alive) -> dict:
    """`now_alive` is the same pid-probe seam as `runs_top_screen.facts`."""
    root = Path(runs_dir)
    pid = runs_top_screen._read_pid(root / f"{run}.pid")
    alive = pid is not None and now_alive(pid)
    fact = runs_top_screen._fact(root, run, alive)
    return {
        "run": run,
        "alive": alive,
        "events": fact["events"],
        "calls": fact["calls"],
        "record": _record(root, run),
        "tail": _tail(root, run),
    }


def draw(stdscr, detail) -> None:
    import curses

    from agent_tools import runs_detail

    stdscr.clear()
    height, width = stdscr.getmaxyx()
    lines = [*runs_detail.render(detail, width), "", "-- static; press q to return --"[:width]]
    for i, line in enumerate(lines[:height]):
        with contextlib.suppress(curses.error):
            stdscr.addnstr(i, 0, line, width)
    stdscr.refresh()
