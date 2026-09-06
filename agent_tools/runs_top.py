"""Rows for `cox runs top`: one row per run in flight, built from its events
and its finished trace calls.

Pure: no filesystem, no subprocess, no clock, no curses. The edge derives
`phases` from `<run>:<phase>.json` manifest names and `calls` from each
trace file's `result` record; this module only ever sees plain data.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_tools.events import Event

__all__ = ["UNSET", "Row", "highlight", "leader_highlight", "order", "render", "row"]

_COLUMNS = ("PHASE", "NODE", "ATT", "TURNS", "COST", "VERDICT", "STATUS", "CEIL", "BY")
_NO_RUNS = "no runs in flight"

UNSET = object()  # `render`'s "no leader argument given" default, distinct from a real `None` (no lock file).


@dataclass(frozen=True)
class Row:
    run: str
    alive: bool
    phase: str
    node: str
    attempt: int
    turns: int
    cost_usd: float
    verdict: str
    status: str
    ceiling: str = ""
    launched_by: str = ""


def _status(events: list[Event], alive: bool, orphaned: bool = False) -> str:
    if any(e.kind == "task_quarantined" for e in events):
        return "quarantined"
    if any(e.kind == "budget_stop" for e in events):
        return "budget"
    if not alive or any(e.kind == "run_exited" for e in events):
        return "exited"
    if orphaned:
        return "orphaned"
    return "running"


def _ceiling_label(ceiling: dict | None) -> str:
    """Pure: the CEIL column's text for a run's parsed ceiling.json (see
    records.ceiling_for), or "" when the run carried no tier/effort overlay."""
    applied = (ceiling or {}).get("applied") or {}
    tier, effort = applied.get("tier") or "", applied.get("effort") or ""
    return "/".join(part for part in (tier, effort) if part)


def _orphaned(alive: bool, launched_by: str, leader: dict | None) -> bool:
    """Alive, and either the leader lost its heartbeat, or a known launcher is not the live leader; no leader file is not an alert."""
    if not alive or leader is None:
        return False
    if leader.get("state") == "stale":
        return True
    return bool(launched_by) and leader.get("state") == "live" and launched_by != leader.get("holder")


def row(run: str, alive: bool, phases: list[str], events: list[Event], calls: list[dict], ceiling: dict | None = None,
        launched_by: str = "", leader: dict | None = None) -> Row:
    """Pure: the one row a run's events and finished calls make."""
    starts = [e for e in events if e.kind == "node_started"]
    verdicts = [e for e in events if e.kind == "verdict"]
    node = starts[-1].detail["node"] if starts else ""
    attempt = starts[-1].detail["attempt"] if starts else 0
    verdict = verdicts[-1].detail["verdict"] if verdicts else ""
    return Row(
        run=run,
        alive=alive,
        phase=phases[-1] if phases else "",
        node=node,
        attempt=attempt,
        turns=sum(c["turns"] for c in calls),
        cost_usd=sum(c["cost_usd"] for c in calls),
        verdict=verdict,
        status=_status(events, alive, _orphaned(alive, launched_by, leader)),
        ceiling=_ceiling_label(ceiling),
        launched_by=launched_by,
    )


def _cut(line: str, width: int) -> str:
    return line[: max(width, 0)]


def order(rows: list) -> list:
    return sorted(rows, key=lambda r: (not r.alive, r.run))


def _leader_line(leader: dict | None) -> str:
    if leader is None:
        return "leader: none"
    return f"leader: {leader['holder']} ({leader['state']}, beat {leader['minutes_ago']}m ago)"


def render(rows: list[Row], width: int, leader=UNSET) -> list[str]:
    """Pure: the leader line (only when `leader` is passed), the header line,
    then one line per row, cut to `width`."""
    prefix = [] if leader is UNSET else [_cut(_leader_line(leader), width)]
    run_width = max([len("RUN")] + [len(r.run) for r in rows])
    header = " ".join(["RUN".ljust(run_width), *_COLUMNS])
    if not rows:
        return [*prefix, _cut(header, width), _cut(_NO_RUNS, width)]
    ordered = order(rows)
    lines = [
        " ".join(
            [
                r.run.ljust(run_width),
                r.phase,
                r.node,
                str(r.attempt),
                str(r.turns),
                f"${r.cost_usd:.2f}",
                r.verdict,
                r.status,
                r.ceiling,
                r.launched_by,
            ]
        )
        for r in ordered
    ]
    return [*prefix, *[_cut(line, width) for line in [header, *lines]]]


def highlight(row: Row) -> str:
    """Pure: the label the screen maps to a colour. Names no colour itself."""
    if row.status in ("quarantined", "budget", "orphaned"):
        return "alert"
    if row.status == "exited":
        return "dim"
    return "normal"


def leader_highlight(leader: dict | None) -> str:
    """Pure: the label the screen maps to a colour for the leader line itself."""
    return "alert" if leader is not None and leader.get("state") == "stale" else "normal"
