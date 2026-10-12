"""Pure rule: which landed main commits turned red and must be reverted; no file, store or clock access."""

from collections.abc import Sequence

from agent_tools.chair_types import Action, LandedMain


def revert_reason(m: LandedMain) -> str | None:
    """The output that justifies a revert, or None. A green main after the land, an infra ci, a pending ci or smoke never counts."""
    if m.get("green_after", False):
        return None
    if m["ci"] == "infra":
        return None
    if m["ci"] == "red":
        return m["ci_output"]
    if m["smoke"] == "failed":
        return m["smoke_output"]
    return None


def _action(m: LandedMain) -> Action | None:
    reason = revert_reason(m)
    if reason is not None:
        return Action(
            kind="revert_land",
            initiative=m["initiative"],
            phase=m["phase"],
            repo=m["repo"],
            pr=m["pr"],
            commit=m["commit"],
            reason=reason,
        )
    if m["ci"] == "infra" and not m.get("green_after", False):
        return Action(
            kind="rerun_main",
            initiative=m["initiative"],
            phase=m["phase"],
            repo=m["repo"],
            commit=m["commit"],
        )
    return None


def revert_check(landed: Sequence[LandedMain]) -> list[Action]:
    """One revert_land per red main and one rerun_main per infra main with no green after it, in input order."""
    return [a for m in landed if (a := _action(m)) is not None]


def revert_stopped(outcomes: Sequence[str]) -> bool:
    """True when the last two outcomes, oldest first, are both reverted."""
    return list(outcomes[-2:]) == ["reverted", "reverted"]
