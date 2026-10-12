"""Which tasks of one initiative can launch now, and what blocks it when none can.

Pure. Takes plain task data and the ordered phase list; no file, store or clock access.
"""

from dataclasses import dataclass

READY = "ready"
DONE = "done"
QUARANTINED = "quarantined"
APPROVED = "approved"  # approved and not yet landed; a landed task is `done`


@dataclass(frozen=True)
class TaskView:
    id: str
    phase: str
    state: str
    needs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Blocking:
    """The earliest phase still holding quarantined or approved-unlanded tasks, with both counts."""

    phase: str
    quarantined: int
    awaiting_land: int


@dataclass(frozen=True)
class WaitingInitiative:
    """The action the plan emits for an initiative that has nothing runnable and is blocked."""

    initiative: str
    blocking: Blocking


@dataclass(frozen=True)
class Runnable:
    """Runnable task ids in input order. An empty tuple means nothing is runnable and nothing blocks."""

    task_ids: tuple[str, ...]


def _blocking_phases(tasks: tuple[TaskView, ...], phases: tuple[str, ...]) -> tuple[Blocking, ...]:
    """Blocking phases in `phases` order; a phase with neither quarantined nor approved tasks is absent."""
    return tuple(
        Blocking(phase, quarantined, awaiting_land)
        for phase in phases
        for quarantined, awaiting_land in (
            (
                sum(1 for t in tasks if t.phase == phase and t.state == QUARANTINED),
                sum(1 for t in tasks if t.phase == phase and t.state == APPROVED),
            ),
        )
        if quarantined or awaiting_land
    )


def runnable_verdict(tasks: tuple[TaskView, ...], phases: tuple[str, ...]) -> Runnable | Blocking:
    """Runnable ids if any task is ready, has all needs done and no earlier blocking phase; else the earliest Blocking, else an empty Runnable."""
    order = {phase: n for n, phase in enumerate(phases)}
    blocking = _blocking_phases(tasks, phases)
    cutoff = order[blocking[0].phase] if blocking else len(phases)
    done = frozenset(t.id for t in tasks if t.state == DONE)
    ids = tuple(
        t.id
        for t in tasks
        if t.state == READY and all(need in done for need in t.needs) and order.get(t.phase, len(phases)) <= cutoff
    )
    if ids:
        return Runnable(ids)
    return blocking[0] if blocking else Runnable(())
