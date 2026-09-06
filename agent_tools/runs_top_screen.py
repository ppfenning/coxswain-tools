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
import socket
import time
from pathlib import Path

from agent_tools import events as events_module
from agent_tools import leader, runs_top
from agent_tools.records import ceiling_for, load_trace

__all__ = ["draw", "facts", "first_visible", "leader_now", "loop", "main", "rows_now"]

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
    """The `result` line's cost and turns for one trace file, or None when
    the name does not fit `<node>-<n>.jsonl` or the file has no result line.
    A file that fails to parse is skipped by `load_trace`, never raised."""
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
    return {"node": m.group(1), "attempt": int(m.group(2)),
            "cost_usd": result.get("total_cost_usd", 0.0), "turns": result.get("num_turns", 0)}


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


def _written_at(path: Path) -> tuple[float, str]:
    """When a trace was last written, name breaking a tie: the node running NOW is the one
    written last, which alphabetical order does not know."""
    try:
        return path.stat().st_mtime, path.name
    except OSError:
        return 0.0, path.name


def _fact(root: Path, run: str, alive: bool) -> dict:
    lines = _read_lines(root / f"{run}.log")
    trace_dir = root / f"{run}-trace"
    trace_paths = sorted(trace_dir.glob("*.jsonl"), key=_written_at) if trace_dir.exists() else []
    names = [p.name for p in trace_paths]
    events = events_module.from_log(run, lines) + events_module.from_trace_names(run, names)
    calls = [c for c in (_call(p) for p in trace_paths) if c is not None]
    return {"run": run, "alive": alive, "phases": _phases(root, run), "events": events, "calls": calls,
            "ceiling": _ceiling(root, run), "launched_by": _launched_by(root, run)}


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
    state = leader.liveness(record, alive, now, socket.gethostname(), heartbeat_minutes)
    return {"holder": record.get("session", ""), "state": state, "minutes_ago": _minutes_ago(record.get("heartbeat_at"), now)}


def rows_now(runs_dir, heartbeat_minutes: int = leader.DEFAULT_HEARTBEAT_MINUTES) -> list[runs_top.Row]:
    leader_state = leader_now(runs_dir, heartbeat_minutes)
    return [runs_top.row(f["run"], f["alive"], f["phases"], f["events"], f["calls"], f["ceiling"], f["launched_by"], leader_state)
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


def _line_kinds(ordered: list, expanded, detail_count: int) -> list:
    """One entry per line `runs_top.render` draws below any leader line: `None`
    for the header or a detail line, `(row, index)` for a row's own line."""
    kinds = [None]
    for i, r in enumerate(ordered):
        kinds.append((r, i))
        if r.run == expanded:
            kinds.extend([None] * detail_count)
    return kinds


def _scroll_facts(ordered: list, expanded, detail_count: int, cursor: int, has_leader: bool) -> tuple[int, int]:
    """The cursor's absolute line number and the total line count, for `first_visible`."""
    kinds = _line_kinds(ordered, expanded, detail_count)
    offset = 1 if has_leader else 0
    cursor_line = offset + next(i for i, k in enumerate(kinds) if k is not None and k[1] == cursor)
    return cursor_line, offset + len(kinds)


def first_visible(cursor_index: int, total_lines: int, window_height: int, current_first: int) -> int:
    """Pure: the scroll offset that keeps `cursor_index` on screen, moving `current_first` no more than it must."""
    if window_height <= 0:
        return current_first
    last_first = max(total_lines - window_height, 0)
    first = min(current_first, last_first)
    if cursor_index < first:
        first = cursor_index
    elif cursor_index > first + window_height - 1:
        first = cursor_index - window_height + 1
    return max(min(first, last_first), 0)


def draw(stdscr, rows: list, cursor: int | None = None, leader_state=runs_top.UNSET,
         expanded=None, detail_lines: tuple = (), first: int = 0) -> None:
    import curses

    stdscr.clear()
    height, width = stdscr.getmaxyx()
    lines = runs_top.render(rows, width, leader_state, expanded, detail_lines)
    has_leader = leader_state is not runs_top.UNSET
    offset = 1 if has_leader else 0
    ordered = _ordered(rows)
    kinds = _line_kinds(ordered, expanded, len(detail_lines))
    has_color = _has_colors()
    if has_color:
        try:
            curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
        except curses.error:
            has_color = False
    for row_i, line in enumerate(lines[first:first + height]):
        i = first + row_i
        if has_leader and i == 0:
            attr = _leader_attr(leader_state, has_color)
        else:
            kind = kinds[i - offset] if 0 <= i - offset < len(kinds) else None
            base = _attr(kind[0], has_color) if kind is not None else curses.A_NORMAL
            attr = base | curses.A_REVERSE if kind is not None and cursor == kind[1] else base
        with contextlib.suppress(curses.error):
            stdscr.addnstr(row_i, 0, line, width, attr)
    stdscr.refresh()


def _session_text(root: Path, run: str) -> str:
    """Edge: the newest trace file's message text and tool calls, one per line."""
    from agent_tools import runs_detail_screen

    newest = runs_detail_screen._newest_trace(root, run)
    if newest is None:
        return ""
    lines = []
    for event in load_trace(newest):
        for item in runs_detail_screen._content(event):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                lines.append(item.get("text", ""))
            elif item.get("type") == "tool_use":
                lines.append(f"tool: {item.get('name', '')}")
    return "\n".join(lines)


def _accordion_detail(runs_dir, run: str, width: int, now_alive) -> list[str]:
    from agent_tools import runs_detail, runs_detail_screen

    root = Path(runs_dir)
    detail = runs_detail.detail(**runs_detail_screen.facts_for(runs_dir, run, now_alive=now_alive))
    tail = runs_top.tail_lines(_session_text(root, run), 3)
    return [*runs_detail.render(detail, width), *tail]


def loop(stdscr, runs_dir, interval: float, tick=rows_now, now_alive=_default_alive,
         heartbeat_minutes: int = leader.DEFAULT_HEARTBEAT_MINUTES) -> int:
    import curses

    with contextlib.suppress(curses.error):  # no real terminal behind stdscr, e.g. under test
        curses.curs_set(0)
    stdscr.timeout(int(interval * 1000))
    cursor = 0
    expanded = None
    first = 0
    while True:
        rows = tick(runs_dir)
        leader_state = leader_now(runs_dir, heartbeat_minutes)
        ordered = _ordered(rows)
        cursor = min(cursor, len(ordered) - 1) if ordered else 0
        expanded = expanded if any(r.run == expanded for r in ordered) else None
        height, width = stdscr.getmaxyx()
        detail_lines = _accordion_detail(runs_dir, expanded, width, now_alive) if expanded is not None else ()
        if ordered:
            cursor_line, total_lines = _scroll_facts(ordered, expanded, len(detail_lines), cursor,
                                                       leader_state is not runs_top.UNSET)
            first = first_visible(cursor_line, total_lines, height, first)
        draw(stdscr, rows, cursor if ordered else None, leader_state, expanded, detail_lines, first)
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q")):
            return 0
        if ordered and ch in (ord("j"), curses.KEY_DOWN):
            cursor = min(cursor + 1, len(ordered) - 1)
        elif ordered and ch in (ord("k"), curses.KEY_UP):
            cursor = max(cursor - 1, 0)
        elif ordered and ch in (10, 13, curses.KEY_ENTER):
            run = ordered[cursor].run
            expanded = None if expanded == run else run
        elif ch == 27:
            expanded = None
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
