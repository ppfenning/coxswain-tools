"""A typed event stream from run records: log lines, trace filenames, usage json.

Pure parsers only — no filesystem, no clock. `seq` is the event's position in
its own source (the log's line number; the trace's attempt number times 1000;
10**9 for the one event a usage file yields) so ordering never depends on when
a parser happened to run.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["Event", "from_log", "from_trace_names", "from_usage"]


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
