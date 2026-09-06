"""A typed event stream from run records: log lines, trace filenames, usage json.
Pure throughout: no filesystem, no clock, and `poll` takes state in and returns state out.
`merge(*streams)` concatenates streams in the order given, keeping each stream's own `seq` order intact.
Callers use the fixed order `merge(log, trace, usage)`; that order is the only cross-source guarantee, and it lands `run_exited_cost` last.
Events from different sources are otherwise not comparable, so nothing here ever interleaves them by seq or by a clock.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["Event", "from_log", "from_trace_names", "from_usage", "merge", "poll"]


@dataclass(frozen=True)
class Event:
    run: str
    kind: str
    seq: int
    detail: dict


_VERDICT_TERSE = re.compile(r"^(\S+) verdict: (\S+)\s*$")
_VERDICT_ARROW = re.compile(r"^\s+(\S+) -> (\S+)\s*$")
_QUARANTINED = re.compile(r"^quarantined task: (.+?) — (.+)$")
_RUN_EXITED = re.compile(r"^epic \S+: (\d+) phase\(s\) complete,.*,\s*(\d+) task\(s\) quarantined")
_TRACE_NAME = re.compile(r"^([A-Za-z0-9_]+)-(\d+)\.jsonl$")
_LEADER_TAKEN = re.compile(r"^leader taken: (\S+) \(pid (\d+)\) on (\S+)\s*$")
_LEADER_RELEASED = re.compile(r"^leader released: (\S+)\s*$")
_LEADER_STALE = re.compile(r"^leader stale: (\S+) \(pid (\d+)\) on (\S+)\s*$")


def from_log(run: str, lines: Sequence[str]) -> list[Event]:
    """Pure: the events a run's log lines state, in source order."""
    events: list[Event] = []
    if lines:
        events.append(Event(run, "run_started", 0, {}))
    for seq, raw in enumerate(lines):
        line = raw.rstrip("\n")
        m = _VERDICT_TERSE.match(line) or _VERDICT_ARROW.match(line)
        if m:
            events.append(Event(run, "verdict", seq, {"node": m.group(1), "verdict": m.group(2)}))
            continue
        m = _QUARANTINED.match(line.strip())
        if m:
            events.append(Event(run, "task_quarantined", seq, {"task": m.group(1), "reason": m.group(2)}))
            continue
        if "error_max_budget_usd" in line or "fix loop stopped: budget" in line:
            events.append(Event(run, "budget_stop", seq, {}))
            continue
        m = _LEADER_TAKEN.match(line)
        if m:
            events.append(Event(run, "leader_taken", seq, {"session": m.group(1), "pid": int(m.group(2)), "host": m.group(3)}))
            continue
        m = _LEADER_RELEASED.match(line)
        if m:
            events.append(Event(run, "leader_released", seq, {"session": m.group(1)}))
            continue
        m = _LEADER_STALE.match(line)
        if m:
            events.append(Event(run, "leader_stale", seq, {"session": m.group(1), "pid": int(m.group(2)), "host": m.group(3)}))
            continue
        m = _RUN_EXITED.search(line)
        if m:
            events.append(Event(run, "run_exited", seq, {"phases_complete": int(m.group(1)), "quarantined": int(m.group(2))}))
    return events


def from_trace_names(run: str, names: Sequence[str]) -> list[Event]:
    """Pure: node_started events from `<node>-<n>.jsonl` trace filenames. A name that does
    not fit that shape is skipped, not raised."""
    events: list[Event] = []
    for name in names:
        m = _TRACE_NAME.match(name)
        if not m:
            continue
        node, attempt = m.group(1), int(m.group(2))
        events.append(Event(run, "node_started", attempt * 1000, {"node": node, "attempt": attempt}))
    return events


def from_usage(run: str, usage: Mapping[str, Any]) -> Event:
    """Pure: the one run_exited_cost event a usage file's summary carries."""
    summary = usage.get("summary") or {}
    return Event(run, "run_exited_cost", 10**9, {"cost_usd": summary.get("cost_usd"), "turns": summary.get("turns")})


def merge(*streams: Sequence[Event]) -> list[Event]:
    """Pure: concatenates streams in the order given. Each stream keeps its own
    order; streams are never interleaved with each other. Call it as
    `merge(log, trace, usage)` for the fixed source order this module promises."""
    return [event for stream in streams for event in stream]


def poll(
    run: str,
    state: Mapping[str, Any],
    new_log_lines: Sequence[str],
    new_trace_names: Sequence[str],
    usage: Mapping[str, Any] | None,
) -> tuple[list[Event], dict]:
    """Pure: folds a new batch of log lines, trace names and an optional usage
    file into `state`, returning the events that batch adds and the next state.
    `run_exited` and `run_exited_cost` are each emitted at most once, tracked
    separately, so a usage file landing after the log's exit line is not lost."""
    seen_lines = state["log_lines_seen"]
    log_batch = [
        Event(e.run, e.kind, e.seq + seen_lines, e.detail)
        for e in from_log(run, new_log_lines)
        if not (e.kind == "run_started" and seen_lines > 0)
    ]
    already_exited = state["emitted_exit"]
    log_events = [e for e in log_batch if not (e.kind == "run_exited" and already_exited)]
    emitted_exit = already_exited or any(e.kind == "run_exited" for e in log_batch)

    seen_names = state["trace_names_seen"]
    unseen_names = [n for n in new_trace_names if n not in seen_names]
    trace_events = from_trace_names(run, unseen_names)

    cost_events = [from_usage(run, usage)] if usage is not None and not state["emitted_cost"] else []
    emitted_cost = state["emitted_cost"] or bool(cost_events)

    new_state = {
        "log_lines_seen": seen_lines + len(new_log_lines),
        "trace_names_seen": frozenset(seen_names) | frozenset(new_trace_names),
        "emitted_exit": emitted_exit,
        "emitted_cost": emitted_cost,
    }
    return merge(log_events, trace_events, cost_events), new_state
