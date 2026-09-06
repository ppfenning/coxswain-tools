"""Curses edge for `cox home`: gathers each panel from the tool behind it,
each on its own timeout, hands the result to `home_model`'s pure frame, draws
it, and runs the effect a keypress's `home_model.step` returns. `curses` is
imported inside each function that needs it, so this module imports on a
machine with no terminal and the gatherers stay testable with a fake stdscr.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from agent_tools import home_model, leader, route, runs_top_screen, usage_window
from agent_tools.pacing import assess

__all__ = ["draw", "facts", "main", "run_effect"]

_TIMEOUT_SECONDS = 2.0
_REFRESH_SECONDS = 2.0
_MARK = "! "


def _read_with_timeout(reader, timeout_seconds: float):
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(reader)
    try:
        return future.result(timeout=timeout_seconds)
    except Exception:
        return None
    finally:
        executor.shutdown(wait=False)  # a genuinely hung reader's thread is abandoned, never joined


def _panel(cache: dict, name: str, reader, timeout_seconds: float, now: float):
    value = _read_with_timeout(reader, timeout_seconds)
    if value is not None:
        return value, "fresh", {**cache, name: (value, now)}
    cached_value, cached_at = cache.get(name, (None, None))
    age = None if cached_at is None else now - cached_at
    return cached_value, home_model.panel_status(cached_value, age, timeout_seconds), cache


def _read_leader(runs_dir) -> dict:
    record = leader.read(runs_dir)
    return record if record is not None else {}


def _read_window(runs_dir, now_dt: datetime) -> dict:
    window = usage_window.gather(runs_dir, now_dt)
    result = assess(window, usage_window.DEFAULT_POLICY, now_dt)
    return {
        "tier": result.tier_ceiling,
        "effort_ceiling": result.effort_ceiling,
        "spent_usd": window.spent_usd,
        "time_to_reset": home_model.time_to_reset(window.end, now_dt),
        "reason": result.reason,
    }


def _backlog(work_dir, intake_dir) -> dict:
    work_dir, intake_dir = Path(work_dir), Path(intake_dir)
    items = [
        route.work_item(route.parse_frontmatter(path.read_text(encoding="utf-8"))[0],
                         initiative=path.parent.parent.name, phase_dir=path.parent.name, stem=path.stem)
        for path in sorted(work_dir.glob("*/*/*.md")) if path.name != "initiative.md"
    ]
    texts = {p.name: (p / "initiative.md").read_text(encoding="utf-8")
             for p in sorted(work_dir.glob("*")) if (p / "initiative.md").is_file()}
    states = route.initiative_states(sorted(texts), items)
    initiatives = [{"id": iid, "done": states[iid], "text": text} for iid, text in texts.items()]
    files = {
        str(path.relative_to(intake_dir)): path.read_text(encoding="utf-8")
        for path in sorted(intake_dir.glob("*.md")) + sorted((intake_dir / "done").glob("*.md"))
    }
    groups = route.intake_groups(route.intake_entries(files), initiatives)
    ready = {summary["id"]: summary["ready"] for summary in route.initiative_summaries(items)}
    return {"queued": len(groups["queued"]), "decomposed": len(groups["decomposed"]),
            "landed": len(groups["landed"]), "ready": ready}


def facts(runs_dir, work_dir, intake_dir, now: float, cache: dict | None = None,
          timeout_seconds: float = _TIMEOUT_SECONDS) -> tuple[home_model.Facts, dict]:
    """One `home_model.Facts` and the cache the next call should pass back in."""
    cache = dict(cache or {})
    now_dt = datetime.fromtimestamp(now, tz=UTC)

    leader_value, leader_status, cache = _panel(cache, "leader", lambda: _read_leader(runs_dir), timeout_seconds, now)
    runs_value, runs_status, cache = _panel(
        cache, "runs", lambda: tuple(runs_top_screen.rows_now(runs_dir)), timeout_seconds, now)
    backlog_value, backlog_status, cache = _panel(
        cache, "backlog", lambda: _backlog(work_dir, intake_dir), timeout_seconds, now)
    window_value, window_status, cache = _panel(
        cache, "window", lambda: _read_window(runs_dir, now_dt), timeout_seconds, now)

    leader_record = leader_value or None
    alive = leader.pid_alive(leader_record["pid"]) if leader_record and isinstance(leader_record.get("pid"), int) else False
    result = home_model.Facts(
        leader=leader_record,
        leader_liveness=leader.liveness(leader_record, alive, now_dt),
        runs_rows=runs_value or (),
        backlog=backlog_value or {},
        window=window_value or {},
        now=now,
    )
    cache["_status"] = {"leader": leader_status, "runs": runs_status, "backlog": backlog_status, "window": window_status}
    return result, cache


def draw(stdscr, facts_obj: home_model.Facts, state: home_model.State, statuses: dict) -> None:
    import curses

    stdscr.clear()
    height, width = stdscr.getmaxyx()
    leader_lines = home_model.leader_pane(facts_obj, width)
    backlog_lines = home_model.backlog_pane(facts_obj, width)
    window_lines = home_model.window_pane(facts_obj, width)
    runs_lines = home_model.runs_pane(facts_obj, width)
    lines = list(home_model.frame(facts_obj, state, width))
    if len(lines) == len(leader_lines) + len(backlog_lines) + len(window_lines) + len(runs_lines):
        offsets = (
            (0, statuses.get("leader", "fresh")),
            (len(leader_lines), statuses.get("backlog", "fresh")),
            (len(leader_lines) + len(backlog_lines), statuses.get("window", "fresh")),
            (len(leader_lines) + len(backlog_lines) + len(window_lines), statuses.get("runs", "fresh")),
        )
        for index, status in offsets:
            if status != "fresh" and index < len(lines):
                lines[index] = f"{_MARK}{lines[index]}"
    for i, line in enumerate(lines[:height]):
        with contextlib.suppress(curses.error):
            stdscr.addnstr(i, 0, line, width)
    stdscr.refresh()


def run_effect(effect, runner=subprocess.run) -> bool:
    """Runs `effect`'s subprocess against a suspended curses screen; returns whether the loop should stop."""
    import curses

    if isinstance(effect, home_model.Quit):
        return True
    if isinstance(effect, home_model.Refuse):
        return False
    if isinstance(effect, home_model.Talk):
        plugin_flag = ["--plugin-dir", effect.plugin_dir] if effect.plugin_dir else []
        argv = ["claude", *plugin_flag, effect.opening]
    elif isinstance(effect, home_model.Setup):
        argv = list(effect.argv)
    else:
        return False
    with contextlib.suppress(curses.error):  # no real terminal behind stdscr, e.g. under test
        curses.endwin()
    try:
        runner(argv)
    finally:
        with contextlib.suppress(curses.error):
            curses.doupdate()
    return False


def main(runs_dir, work_dir, intake_dir, plugin_dir: str, refresh_seconds: float = _REFRESH_SECONDS) -> int:
    import curses

    def _loop(stdscr):
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        stdscr.timeout(int(refresh_seconds * 1000))
        cache: dict = {}
        state = home_model.State(plugin_dir=plugin_dir, leader_liveness="none", other_holder=None)
        while True:
            facts_obj, cache = facts(runs_dir, work_dir, intake_dir, time.time(), cache)
            other_holder = facts_obj.leader.get("session") if facts_obj.leader and facts_obj.leader_liveness == "live" else None
            state = home_model.State(plugin_dir=plugin_dir, leader_liveness=facts_obj.leader_liveness, other_holder=other_holder)
            draw(stdscr, facts_obj, state, cache.get("_status", {}))
            ch = stdscr.getch()
            if ch == -1:
                continue
            key = chr(ch) if 0 <= ch < 256 else ""
            state, effect = home_model.step(state, key)
            if effect is not None and run_effect(effect):
                return 0

    return curses.wrapper(_loop)
