"""`route requeue` core: queue the intake of an initiative with no live tasks and drop the empty initiative. The edge reads the store and writes; the planning is pure."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_tools import route_guard, run_store

DONE = route_guard.DONE
REFUSED = route_guard.REFUSED
QUEUED = "queued"
DROPPED = "dropped"


@dataclass(frozen=True)
class Plan:
    writes: tuple[dict, ...]  # partial rows for run_store.upsert_row_detail; empty when nothing is left to do
    note: str


@dataclass(frozen=True)
class Refused:
    reason: str


def _extra(row: Mapping) -> Mapping:
    return row.get("extra") or {}


def _intake_named(rows: Sequence[Mapping], id: str) -> Mapping | None:
    """An intake row in any state: resolve_target sees only queued ones, and a decomposed intake is not queued."""
    return next((r for r in rows if r.get("kind") == "intake" and id in (r.get("task_id"), _extra(r).get("id"))), None)


def _initiative_row(rows: Sequence[Mapping], name: str) -> Mapping | None:
    return next((r for r in rows if r.get("kind") == "initiative" and r.get("task_id") == name), None)


def _intake_of(rows: Sequence[Mapping], initiative: Mapping) -> Mapping | None:
    """The intake that stamped this initiative: its `extra.initiative` names it, or the initiative's `extra.intake` path names the intake."""
    stem = str(_extra(initiative).get("intake") or "").rsplit("/", 1)[-1].removesuffix(".md")
    return next(
        (
            r
            for r in rows
            if r.get("kind") == "intake"
            and (_extra(r).get("initiative") == initiative["task_id"] or r.get("task_id") == stem)
        ),
        None,
    )


def find_pair(rows: Sequence[Mapping], id: str) -> tuple[Mapping, Mapping] | None:
    """(intake row, initiative row) for an id naming either; None when the id or the link between them is not in `rows`."""
    target = route_guard.resolve_target(rows, id)
    if isinstance(target, route_guard.InitiativeFound):
        intake, name = None, target.initiative
    else:  # an empty initiative has no task rows, so resolve_target calls it NotFound; the initiative row is looked up by name
        intake = _intake_named(rows, target.task_id if isinstance(target, route_guard.QueuedIntake) else id)
        name = _extra(intake).get("initiative") if intake is not None else id
    initiative = _initiative_row(rows, str(name))
    if intake is None and initiative is not None:
        intake = _intake_of(rows, initiative)
    return (intake, initiative) if intake is not None and initiative is not None else None


def live_tasks(rows: Sequence[Mapping], initiative: str) -> list[Mapping]:
    """The initiative's task rows whose state is not dropped."""
    return [
        r
        for r in rows
        if r.get("kind", "task") == "task" and r.get("initiative") == initiative and r.get("state") != DROPPED
    ]


def _state_row(row: Mapping, state: str) -> dict:
    return {"initiative": row["initiative"], "task_id": row["task_id"], "state": state}


def plan_requeue(rows: Sequence[Mapping], id: str) -> Plan | Refused:
    """Pure. Refuses an unknown id and an initiative with a task that is not dropped; otherwise the rows still to write."""
    pair = find_pair(rows, id)
    if pair is None:
        return Refused(f"no intake or initiative named {id}, or no intake linked to the initiative")
    intake, initiative = pair
    live = live_tasks(rows, str(initiative["task_id"]))
    if live:
        named = ", ".join(f"{t.get('task_id')} ({t.get('state')})" for t in live)
        return Refused(f"initiative {initiative['task_id']} still has tasks that are not dropped: {named}")
    writes = tuple(
        _state_row(row, state) for row, state in ((intake, QUEUED), (initiative, DROPPED)) if row.get("state") != state
    )
    if not writes:
        return Plan(
            (),
            f"already requeued: intake {intake['task_id']} is queued and initiative {initiative['task_id']} is dropped",
        )
    return Plan(writes, f"intake {intake['task_id']} queued, initiative {initiative['task_id']} dropped")


def requeue(workspace: Path, runs_dir: Path, id: str, *, dry_run: bool = False) -> tuple[int, str]:
    """Edge. (0, message) done or already done, (2, message) refused. Writes store rows only: no file under `workspace` is touched."""
    plan = plan_requeue(run_store.read_queue(runs_dir), id)
    if isinstance(plan, Refused):
        return REFUSED, f"requeue: refused: {plan.reason}"
    if not plan.writes or dry_run:
        return DONE, plan.note if not plan.writes else f"would requeue: {plan.note}"
    for row in plan.writes:
        reason = run_store.upsert_row_detail(runs_dir, row)
        if reason:
            return REFUSED, f"requeue: the store did not take {row['task_id']}: {reason}"
    return DONE, f"requeue: {plan.note}"
