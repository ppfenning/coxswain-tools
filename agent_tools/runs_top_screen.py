"""Curses edge for `cox runs top`: reads the facts a running harness leaves on
disk, maps them through the pure `runs_top` model, and draws the result.
`curses` is imported inside each function that needs it, so this module
imports on a machine with no terminal and the pure pieces stay testable with
a fake stdscr.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import time
from pathlib import Path

from agent_tools import events as events_module
from agent_tools import leader, runs_top
from agent_tools.records import ceiling_for, load_trace, load_usage, usage_summary

__all__ = ["draw", "facts", "leader_now", "loop", "main", "rows_now"]

_STALE_SECONDS = 600
_TRACE_NAME = re.compile(r"^([A-Za-z0-9_]+)-(\d+)$")
_MANIFEST_NAME = re.compile(r"^[^:]+:(.+)\.json$")


def _default_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid(path: Path):
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _phases(root: Path, run: str) -> list[str]:
    paths = sorted(root.glob(f"{run}:*.json"), key=lambda p: p.stat().st_mtime)
    names = (_MANIFEST_NAME.match(p.name) for p in paths)
    return [m.group(1) for m in names if m]


def _call(path: Path) -> dict | None:
    """The `result` line's turns for one trace file, or None when the name
    does not fit `<node>-<n>.jsonl` or the file has no result line. A file
    that fails to parse is skipped by `load_trace`, never raised."""
    m = _TRACE_NAME.match(path.stem)
    if not m:
        return None
    try:
        trace_events = load_trace(path)
    except OSError:
        return None
    result = next((e for e in trace_events if e.get("type") == "result"), None)
    if result is None:
        return None
    return {"node": m.group(1), "attempt": int(m.group(2)), "turns": result.get("num_turns", 0)}


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []


def _ceiling(root: Path, run: str) -> dict | None:
    path = root / f"{run}.ceiling.json"
    if not path.exists():
        return None
    return ceiling_for(run, {path.name: path.read_text(encoding="utf-8")})


def _launched_by(root: Path, run: str) -> str:
    path = root / f"{run}.launched.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return data.get("launched_by", "") if isinstance(data, dict) else ""


def _tokens(root: Path, run: str) -> int | None:
    """The run's input tokens, cache reads included, from `<run>.usage.json`, or None when the file is absent or unreadable."""
    path = root / f"{run}.usage.json"
    if not path.exists():
        return None
    try:
        return usage_summary(load_usage(path))["tokens_total"]
    except (OSError, json.JSONDecodeError):
        return None


def _fact(root: Path, run: str, alive: bool) -> dict:
    lines = _read_lines(root / f"{run}.log")
    trace_dir = root / f"{run}-trace"
    trace_paths = sorted(trace_dir.glob("*.jsonl")) if trace_dir.exists() else []
    names = [p.name for p in trace_paths]
    events = events_module.from_log(run, lines) + events_module.from_trace_names(run, names)
    calls = [c for c in (_call(p) for p in trace_paths) if c is not None]
    return {"run": run, "alive": alive, "phases": _phases(root, run), "events": events, "calls": calls,
            "ceiling": _ceiling(root, run), "launched_by": _launched_by(root, run), "tokens": _tokens(root, run)}


def facts(runs_dir, now_alive=_default_alive) -> list[dict]:
    """Every run still worth a line on screen: a `.pid` that probes alive,
    plus a `.log` whose `.pid` is missing or dead but was touched in the
    last ten minutes, so a run that just exited stays on screen briefly."""
    root = Path(runs_dir)
    pids = {p.stem: _read_pid(p) for p in root.glob("*.pid")}
    alive = {run for run, pid in pids.items() if pid is not None and now_alive(pid)}
    now = time.time()
    recent = {
        p.stem for p in root.glob("*.log")
        if p.stem not in alive and now - p.stat().st_mtime <= _STALE_SECONDS
    }
    return [_fact(root, run, run in alive) for run in sorted(alive | recent)]


def _minutes_ago(heartbeat_at, now: datetime.datetime) -> int:
    try:
        beat = datetime.datetime.fromisoformat(heartbeat_at)
    except (TypeError, ValueError):
        return 0
    return max(int((now - beat).total_seconds() // 60), 0)


def leader_now(runs_dir, heartbeat_minutes: int = leader.DEFAULT_HEARTBEAT_MINUTES, pid_alive=leader.pid_alive) -> dict | None:
    """Edge: `runs/leader.json` turned into the plain dict `runs_top.render` shows,
    or None when no lock is held. A file present but unreadable reads as no lock,
    same as `leader.read`'s own contract for a missing file."""
    try:
        record = leader.read(runs_dir)
    except (OSError, json.JSONDecodeError):
        record = None
    if record is None:
        return None
    now = datetime.datetime.now(datetime.UTC)
    alive = pid_alive(record["pid"]) if isinstance(record.get("pid"), int) else False
    state = leader.liveness(record, alive, now, heartbeat_minutes)
    return {"holder": record.get("session", ""), "state": state, "minutes_ago": _minutes_ago(record.get("heartbeat_at"), now)}


def rows_now(runs_dir, heartbeat_minutes: int = leader.DEFAULT_HEARTBEAT_MINUTES) -> list[runs_top.Row]:
    leader_state = leader_now(runs_dir, heartbeat_minutes)
    return [runs_top.row(f["run"], f["alive"], f["phases"], f["events"], f["calls"], f["ceiling"], f["launched_by"], leader_state, f["tokens"])
            for f in facts(runs_dir)]


def _has_colors() -> bool:
    import curses

    try:
        return curses.has_colors()
    except curses.error:
        return False  # no real terminal behind stdscr, e.g. under test


def _attr(row, has_color: bool):
    import curses

    label = runs_top.highlight(row)
    if label == "alert":
        return (curses.color_pair(1) if has_color else 0) | curses.A_BOLD
    if label == "dim":
        return curses.A_DIM
    return curses.A_NORMAL


def _leader_attr(leader_state, has_color: bool):
    import curses

    if runs_top.leader_highlight(leader_state) == "alert":
        return (curses.color_pair(1) if has_color else 0) | curses.A_BOLD
    return curses.A_NORMAL


def _ordered(rows: list) -> list:
    return runs_top.order(rows)


def draw(stdscr, rows: list, cursor: int | None = None, leader_state=runs_top.UNSET) -> None:
    import curses

    stdscr.clear()
    height, width = stdscr.getmaxyx()
    lines = runs_top.render(rows, width, leader_state)
    offset = 0 if leader_state is runs_top.UNSET else 1
    ordered = _ordered(rows)
    has_color = _has_colors()
    if has_color:
        try:
            curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
        except curses.error:
            has_color = False
    for i, line in enumerate(lines[:height]):
        if offset and i == 0:
            attr = _leader_attr(leader_state, has_color)
        else:
            row = ordered[i - 1 - offset] if offset < i <= len(ordered) + offset else None
            base = _attr(row, has_color) if row is not None else curses.A_NORMAL
            attr = base | curses.A_REVERSE if row is not None and cursor == i - 1 - offset else base
        with contextlib.suppress(curses.error):
            stdscr.addnstr(i, 0, line, width, attr)
    stdscr.refresh()


def _show_detail(stdscr, runs_dir, run: str, now_alive=_default_alive) -> None:
    """Draws one run's detail until `q`/ESC; the caller resumes the table."""
    from agent_tools import runs_detail, runs_detail_screen

    detail = runs_detail.detail(**runs_detail_screen.facts_for(runs_dir, run, now_alive=now_alive))
    while True:
        runs_detail_screen.draw(stdscr, detail)
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q"), 27):
            return


def loop(stdscr, runs_dir, interval: float, tick=rows_now, now_alive=_default_alive,
         heartbeat_minutes: int = leader.DEFAULT_HEARTBEAT_MINUTES) -> int:
    import curses

    with contextlib.suppress(curses.error):  # no real terminal behind stdscr, e.g. under test
        curses.curs_set(0)
    stdscr.timeout(int(interval * 1000))
    cursor = 0
    while True:
        rows = tick(runs_dir)
        leader_state = leader_now(runs_dir, heartbeat_minutes)
        ordered = _ordered(rows)
        cursor = min(cursor, len(ordered) - 1) if ordered else 0
        draw(stdscr, rows, cursor if ordered else None, leader_state)
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q")):
            return 0
        if ordered and ch in (ord("j"), curses.KEY_DOWN):
            cursor = min(cursor + 1, len(ordered) - 1)
        elif ordered and ch in (ord("k"), curses.KEY_UP):
            cursor = max(cursor - 1, 0)
        elif ordered and ch in (10, 13, curses.KEY_ENTER):
            _show_detail(stdscr, runs_dir, ordered[cursor].run, now_alive)
        # curses.KEY_RESIZE and a plain timeout both fall through here: either
        # way the next iteration redraws against the current rows and size.


def main(runs_dir, interval: float, heartbeat_minutes: int = leader.DEFAULT_HEARTBEAT_MINUTES) -> int:
    import curses
    import signal

    def _hangup(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGHUP, _hangup)
    try:
        return curses.wrapper(lambda stdscr: loop(stdscr, runs_dir, interval, heartbeat_minutes=heartbeat_minutes))
    except KeyboardInterrupt:
        # A closed window sends SIGHUP; raising through the wrapper lets it
        # restore the terminal before this function returns.
        return 0
    finally:
        signal.signal(signal.SIGHUP, previous)
