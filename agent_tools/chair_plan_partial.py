"""Pure core of the partial land: whether a phase with approved and quarantined tasks lands what it can."""
from collections.abc import Sequence
from typing import Literal

from agent_tools.chair_types import Action

TaskState = tuple[str, str]  # (task id, state), in the phase's order
PhaseVerdict = Literal["land_phase", "partial_land", "wait"]

# Siblings that never block a land. `quarantined` is the work-item state read in chair_read_quarantined.py; any other
# state (ready, running, review, pending) is unfinished work, and the phase waits for it.
_NOT_BLOCKING = frozenset({"approved", "quarantined", "done", "dropped"})


def classify_phase(tasks: Sequence[TaskState]) -> PhaseVerdict:
    """A phase needs an approved task; any unfinished sibling makes it wait; a quarantined sibling makes the land partial."""
    states = [state for _, state in tasks]
    if "approved" not in states or not all(state in _NOT_BLOCKING for state in states):
        return "wait"
    return "partial_land" if "quarantined" in states else "land_phase"


def partial_land_action(initiative: str, phase: str, tasks: Sequence[TaskState]) -> Action | None:
    """The partial_land naming the approved task ids in the given order and the quarantined count; None for any other verdict."""
    if classify_phase(tasks) != "partial_land":
        return None
    return {
        "kind": "partial_land",
        "initiative": initiative,
        "phase": phase,
        "task_ids": [task_id for task_id, state in tasks if state == "approved"],
        "quarantined": sum(1 for _, state in tasks if state == "quarantined"),
    }
