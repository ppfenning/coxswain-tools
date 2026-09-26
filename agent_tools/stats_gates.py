"""Gate metrics: what each review, validation and plan gate costs and how often it changes the outcome.

Pure. Task rows and call rows arrive already filtered by the caller; nothing here reads a database, clock or file.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

GATE_ROLES = (
    "handoff",
    "review_charter",
    "review_adversary",
    "arbitrate",
    "validate_chunk",
    "validate_phase",
    "plan_attack",
    "plan_competition",
)

# The task column carrying each role's verdict. None: the records carry no verdict for that role.
# plan_attack and plan_competition share plan_gate_verdict, so their verdict_mix, changed and caught are the same
# numbers; only cost differs. The records do not say which of the two the plan gate followed.
VERDICT_COLUMN: Mapping[str, str | None] = {
    "handoff": "handoff_verdict",
    "review_charter": "charter_verdict",
    "review_adversary": "adversary_verdict",
    "arbitrate": "arbiter_verdict",
    "validate_chunk": None,
    "validate_phase": None,
    "plan_attack": "plan_gate_verdict",
    "plan_competition": "plan_gate_verdict",
}

APPROVE = "approve"
# The review enum is stats_examples.VERDICTS: approve, revise, reject.
_REVIEW = frozenset({APPROVE}), frozenset({"revise", "reject"})
# (passing, objecting) verdicts per role. Any other string is unknown and yields no changed fact, never a guess.
# handoff: stats_ingest._handoff_verdict writes only "yes" and "no".
# plan gate: only "pass" is attested, in tests/test_stats_ingest.py; "fail" and "revise" are unmeasured.
VERDICTS: Mapping[str, tuple[frozenset[str], frozenset[str]]] = {
    "handoff": (frozenset({"yes"}), frozenset({"no"})),
    "review_charter": _REVIEW,
    "review_adversary": _REVIEW,
    "arbitrate": _REVIEW,
    "plan_attack": (frozenset({"pass"}), frozenset({"fail", "revise"})),
    "plan_competition": (frozenset({"pass"}), frozenset({"fail", "revise"})),
}

REVIEW_ROLES = ("review_charter", "review_adversary")
LOW_VALUE_RATE = 0.05
MIN_SAMPLE = 50  # decided tasks a role needs before it is judged

Task = Mapping[str, Any]


def _rebuilt(task: Task) -> bool | None:
    """True on a second build or a stop; False only when both fix-loop facts are stated; None when the record is silent."""
    attempts, stopped = task.get("fix_loop_attempts"), task.get("fix_loop_stopped")
    if (attempts is not None and attempts > 1) or stopped == 1:
        return True
    return False if attempts is not None and stopped is not None else None


def _changed(role: str, task: Task) -> bool | None:
    """None when the task carries no verdict, an unknown verdict, or an objection with no stated fix-loop outcome."""
    column = VERDICT_COLUMN[role]
    verdict = task.get(column) if column else None
    passing, objecting = VERDICTS.get(role, (frozenset(), frozenset()))
    # Changed is an objecting verdict followed by fix_loop_attempts > 1 or fix_loop_stopped == 1. The fix loop only
    # runs after an objection, so a stop recorded in it followed that objection. The verdict field:
    # handoff: handoff_verdict "no".
    # review_charter: charter_verdict "revise" or "reject".
    # review_adversary: adversary_verdict "revise" or "reject".
    # arbitrate: arbiter_verdict "revise" or "reject".
    # validate_chunk: no verdict field, so always None.
    # validate_phase: no verdict field, so always None.
    # plan_attack: plan_gate_verdict "fail" or "revise".
    # plan_competition: plan_gate_verdict "fail" or "revise".
    if verdict in passing:
        return False
    if verdict in objecting:
        return _rebuilt(task)
    return None


def _key(row: Task) -> tuple[Any, Any]:
    return (row.get("run_id"), row.get("task_id"))


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _role_row(role: str, calls: Sequence[Task], tasks_by_key: Mapping[tuple[Any, Any], Task]) -> dict[str, Any]:
    cost = sum(call.get("cost_usd") or 0 for call in calls)
    keys = {_key(call) for call in calls if call.get("task_id") is not None}
    joined = [tasks_by_key[key] for key in sorted(keys, key=repr) if key in tasks_by_key]
    column = VERDICT_COLUMN[role]
    with_verdict = [t for t in joined if column and t.get(column) is not None]
    decided = [(t, c) for t in with_verdict if (c := _changed(role, t)) is not None]
    changed = [t for t, c in decided if c]
    # A task with a verdict but no known outcome (an unknown verdict, or an objection with no stated fix-loop outcome)
    # is left out of changed, caught and the rate, and counted in `excluded`: one silent legacy record must not void
    # what the others state. The rate is None only when no task has a known outcome.
    rate = _ratio(len(changed), len(decided))
    both_approved = [
        t for t in joined if t.get("charter_verdict") == APPROVE and t.get("adversary_verdict") == APPROVE
    ]
    return {
        "role": role,
        "calls": len(calls),
        "cost": cost,
        "cost_per_task": _ratio(cost, len(keys)),
        "verdict_mix": dict(Counter(t[column] for t in with_verdict)) if with_verdict else None,
        "changed": len(changed) if decided else None,
        "verdict_tasks": len(with_verdict),
        "excluded": len(with_verdict) - len(decided),
        "decided": len(decided),
        "changed_rate": rate,
        "caught": sum(1 for t in changed if t.get("outcome") == "landed") if decided else None,
        "agreed": len(both_approved) if role in REVIEW_ROLES and with_verdict else None,
        "cost_per_changed": _ratio(cost, len(changed)),
    }


def gate_rows(tasks: Sequence[Task], calls: Sequence[Task]) -> list[dict[str, Any]]:
    """One row per gate role that has a call, in GATE_ROLES order; tasks join calls on (run_id, task_id)."""
    tasks_by_key = {_key(task): task for task in tasks}
    by_role = {role: [c for c in calls if c.get("role") == role] for role in GATE_ROLES}
    return [_role_row(role, by_role[role], tasks_by_key) for role in GATE_ROLES if by_role[role]]


def verdict_line(row: Task, min_sample: int = MIN_SAMPLE) -> str:
    rate = row["changed_rate"]
    if rate is None:
        return f"{row['role']}: the record carries no outcome data."
    if row["decided"] < min_sample:
        return f"{row['role']}: not enough data ({row['decided']} decided tasks, need {min_sample})"
    if rate < LOW_VALUE_RATE:
        return f"{row['role']}: candidate for a cheaper tier or for skipping on small tasks."
    return f"{row['role']}: the step is earning its cost."
