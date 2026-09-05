"""Reads a run's own records off disk and drives `events.poll` with them.
This module does no parsing or deciding: it only opens files (log, trace
filenames, usage json) and hands their bytes to `poll`, which is pure.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from agent_tools.events import poll

__all__ = ["events"]


def _initial_state() -> dict:
    return {
        "log_lines_seen": 0,
        "trace_names_seen": frozenset(),
        "emitted_exit": False,
        "emitted_cost": False,
    }


def _read_complete_lines(path: Path) -> list[str]:
    """The log's lines that end in a newline. A writer's last line, still open,
    is held back rather than counted as seen, so a later pass delivers it whole."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        return lines[:-1]
    return lines


def _trace_names(root: Path, run: str) -> list[str]:
    trace_dir = root / f"{run}-trace"
    if not trace_dir.exists():
        return []
    return sorted(p.name for p in trace_dir.glob("*.jsonl"))


def _usage(root: Path, run: str, already_emitted: bool):
    """The usage file's parsed contents, or None if it is absent, already spent,
    or caught mid-write: a torn write is not yet a usage file, so it is retried
    on the next pass rather than raised."""
    if already_emitted:
        return None
    path = root / f"{run}.usage.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _done(state: dict) -> bool:
    """A run stops being followed once `run_exited` has fired. Nothing in this
    repository writes `<run>.usage.json`, so waiting for `run_exited_cost` as
    well would follow an exited run forever; a usage file present in the same
    pass as the exit line is still emitted, because `poll` sees it first."""
    return bool(state["emitted_exit"])


def events(runs_dir, follow: bool = False, sleep=time.sleep):
    """Yields `Event`s for every `<run>.log` under `runs_dir`, driving `poll` with
    each run's bytes: the whole log and trace list on the first pass, then only
    the log's new complete lines on each later pass. `poll` owns every decision,
    including which trace names are new, so the full current listing is passed
    each time rather than filtered here. With `follow`, sleeps between passes
    and keeps polling a run until it is `_done`. The run set itself is fixed at
    the first pass: a `<run>.log` created afterwards is not picked up."""
    root = Path(runs_dir)
    run_names = sorted(p.stem for p in root.glob("*.log"))
    state: dict[str, dict] = {}
    open_runs: set[str] = set()
    for run in run_names:
        lines = _read_complete_lines(root / f"{run}.log")
        names = _trace_names(root, run)
        usage = _usage(root, run, already_emitted=False)
        batch, new_state = poll(run, _initial_state(), lines, names, usage)
        state[run] = new_state
        yield from batch
        if not _done(new_state):
            open_runs.add(run)

    while follow and open_runs:
        sleep(2)
        for run in list(open_runs):
            st = state[run]
            complete_lines = _read_complete_lines(root / f"{run}.log")
            # A log shorter than what has already been seen was truncated or
            # recreated: start that run's line count over, so the lines present
            # are emitted once with fresh seqs rather than on every later pass.
            st = st if len(complete_lines) >= st["log_lines_seen"] else {**st, "log_lines_seen": 0}
            seen = st["log_lines_seen"]
            new_lines = complete_lines[seen:]
            names = _trace_names(root, run)
            usage = _usage(root, run, already_emitted=st["emitted_cost"])
            batch, new_state = poll(run, st, new_lines, names, usage)
            state[run] = new_state
            yield from batch
            if _done(new_state):
                open_runs.discard(run)
