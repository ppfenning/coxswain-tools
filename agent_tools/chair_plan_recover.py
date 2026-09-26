"""Plan the recovery half of a chair tick: relaunches, harness retries and needs-the-chair reports.

Pure. Takes the facts, returns actions. No pacing, no run history, no I/O.
"""
from agent_tools.chair_types import Action, Facts, InitiativeFacts, QuarantineFacts


def _is_retry(q: QuarantineFacts) -> bool:
    # harness_failures == 0 on a harness cause is not a case the rules name; the chair looks at it.
    return q["cause"] == "harness" and q["harness_failures"] == 1


def _needs_chair_initiatives(quarantines: list[QuarantineFacts]) -> set[str]:
    return {q["initiative"] for q in quarantines if not _is_retry(q)}


def _quarantine_actions(quarantines: list[QuarantineFacts]) -> list[Action]:
    chair = _needs_chair_initiatives(quarantines)
    return [
        {"kind": "needs_chair", "initiative": q["initiative"], "cause": q["cause"]}
        if not _is_retry(q)
        else {"kind": "retry", "task_id": q["task_id"], "initiative": q["initiative"]}
        for q in quarantines
        if not _is_retry(q) or q["initiative"] not in chair
    ]


def _can_relaunch(i: InitiativeFacts, blocked: set[str]) -> bool:
    return (
        i["started"]
        and i["id"] not in blocked
        and bool(i["ready_tasks"])
        and all(need in i["landed"] for t in i["ready_tasks"] for need in t["needs"])
    )


def _relaunch_actions(initiatives: list[InitiativeFacts], blocked: set[str]) -> list[Action]:
    return [
        action
        for i in initiatives
        if _can_relaunch(i, blocked)
        for action in ({"kind": "clear_branches", "initiative": i["id"]}, {"kind": "relaunch", "initiative": i["id"]})
    ]


def plan_recover(facts: Facts) -> list[Action]:
    """Quarantine actions in input order, then relaunch pairs. An open quarantine blocks its initiative's relaunch."""
    quarantines = facts["quarantines"]
    blocked = {q["initiative"] for q in quarantines}
    return _quarantine_actions(quarantines) + _relaunch_actions(facts["initiatives"], blocked)
