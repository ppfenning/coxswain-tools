"""Pure classifier for how an ended run ended: the wire value of a feed run row's `end`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_tools.dash_history import first_cause, outcome_cause, run_outcome
from agent_tools.run_death_cause import death_cause

Row = Mapping[str, Any]
End = str | dict[str, str]

NO_EXIT_RECORD = "no exit record"
BARE_KINDS = ("landed", "approved", "idle", "stopped")
RECORDED_IDLE = ("blocked", "waiting")


def _died(cause: str) -> dict[str, str]:
    return {"kind": "died", "cause": cause}


def _crash_cause(run: Row, log_tail: str) -> str:
    """The schema refusal in the log tail, else the store status, else `NO_EXIT_RECORD`."""
    return death_cause(log_tail) or str(run.get("status") or NO_EXIT_RECORD)


def run_end(run: Row, run_tasks: Sequence[Row], log_tail: str) -> End:
    """A bare kind, or `{kind, cause}` for quarantined and died. Never `running`: the feed sets that for live lanes."""
    outcome = run_outcome(run, run_tasks)
    if run.get("status") == "lost" and outcome not in ("landed", "approved"):
        return _died("lost")
    if outcome in BARE_KINDS:
        return outcome
    if outcome == "quarantined":
        return {"kind": "quarantined", "cause": first_cause(run_tasks)}
    if outcome in RECORDED_IDLE:
        return "idle"
    if outcome == "refused":
        return _died(outcome_cause(run)[1])
    return _died(_crash_cause(run, log_tail))
