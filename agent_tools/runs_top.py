"""Rows for `cox runs top`: one row per run in flight, built from its events
and its finished trace calls.

Pure: no filesystem, no subprocess, no clock, no curses. The edge derives
`phases` from `<run>:<phase>.json` manifest names and `calls` from each
trace file's `result` record; this module only ever sees plain data.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_tools.events import Event

__all__ = ["Row", "row", "render", "highlight"]

_COLUMNS = ("PHASE", "NODE", "ATT", "TURNS", "COST", "VERDICT", "STATUS")
_NO_RUNS = "no runs in flight"


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


def _status(events: list[Event], alive: bool) -> str:
    if any(e.kind == "task_quarantined" for e in events):
        return "quarantined"
    if any(e.kind == "budget_stop" for e in events):
        return "budget"
    if not alive or any(e.kind == "run_exited" for e in events):
        return "exited"
    return "running"


def row(run: str, alive: bool, phases: list[str], events: list[Event], calls: list[dict]) -> Row:
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
        status=_status(events, alive),
    )


def _cut(line: str, width: int) -> str:
    return line[: max(width, 0)]


def render(rows: list[Row], width: int) -> list[str]:
    """Pure: the header line then one line per row, cut to `width`."""
    run_width = max([len("RUN")] + [len(r.run) for r in rows])
    header = " ".join(["RUN".ljust(run_width), *_COLUMNS])
    if not rows:
        return [_cut(header, width), _cut(_NO_RUNS, width)]
    ordered = sorted(rows, key=lambda r: (not r.alive, r.run))
    lines = [
        " ".join(
            [
                r.run.ljust(run_width),
                r.phase,
                r.node,
                str(r.attempt),
                str(r.turns),
                "$%.2f" % r.cost_usd,
                r.verdict,
                r.status,
            ]
        )
        for r in ordered
    ]
    return [_cut(line, width) for line in [header, *lines]]


def highlight(row: Row) -> str:
    """Pure: the label the screen maps to a colour. Names no colour itself."""
    if row.status in ("quarantined", "budget"):
        return "alert"
    if row.status == "exited":
        return "dim"
    return "normal"
