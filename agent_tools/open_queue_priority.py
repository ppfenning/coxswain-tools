"""Pure core for open-queue priority: which rows are open, and what priority puts work top, bottom or one step."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple

OPEN_TASK_STATES = frozenset({"ready", "approved", "quarantined"})
OPEN_INTAKE_STATES = frozenset({"queued"})


class Row(NamedTuple):
    initiative_id: str
    kind: str  # "task" or "intake"
    state: str
    priority: int


@dataclass(frozen=True)
class Target:
    """`measured` is the open max (top) or min (bottom) of other initiatives, None when none is open."""

    measured: int | None
    value: int


def _is_open(row: Row) -> bool:
    if row.kind == "task":
        return row.state in OPEN_TASK_STATES
    if row.kind == "intake":
        return row.state in OPEN_INTAKE_STATES
    return False


def open_rows(rows: Iterable[Row]) -> list[Row]:
    return [r for r in rows if _is_open(r)]


def open_queue(rows: Iterable[Row]) -> list[tuple[int, str, int]]:
    """One (highest open priority, initiative_id, open_count) per initiative, priority descending then id ascending."""
    opened = open_rows(rows)
    ids = {r.initiative_id for r in opened}
    entries = [
        (
            max(r.priority for r in opened if r.initiative_id == i),
            i,
            sum(1 for r in opened if r.initiative_id == i),
        )
        for i in ids
    ]
    return sorted(entries, key=lambda e: (-e[0], e[1]))


def _current(rows: Iterable[Row], initiative_id: str) -> int:
    """Highest priority over the initiative's own rows, open or not; 0 when it has none."""
    return max((r.priority for r in rows if r.initiative_id == initiative_id), default=0)


def _others_open(rows: Iterable[Row], initiative_id: str) -> list[int]:
    return [r.priority for r in open_rows(rows) if r.initiative_id != initiative_id]


def target_for_top(rows: list[Row], initiative_id: str) -> Target:
    current = _current(rows, initiative_id)
    others = _others_open(rows, initiative_id)
    if not others:
        return Target(None, current)
    measured = max(others)
    return Target(measured, current if current > measured else measured + 1)


def target_for_bottom(rows: list[Row], initiative_id: str) -> Target:
    current = _current(rows, initiative_id)
    others = _others_open(rows, initiative_id)
    if not others:
        return Target(None, current)
    measured = min(others)
    return Target(measured, current if current < measured else measured - 1)


def target_for_up(rows: list[Row], initiative_id: str) -> int:
    """Pass the nearest higher open neighbour; with none higher, current plus one."""
    current = _current(rows, initiative_id)
    higher = [p for p in _others_open(rows, initiative_id) if p > current]
    return (min(higher) if higher else current) + 1


def target_for_down(rows: list[Row], initiative_id: str) -> int:
    """Pass the nearest lower open neighbour; with none lower, current minus one."""
    current = _current(rows, initiative_id)
    lower = [p for p in _others_open(rows, initiative_id) if p < current]
    return (max(lower) if lower else current) - 1


def format_listing(entries: Iterable[tuple[int, str, int]]) -> list[str]:
    return [f"{priority}  {initiative_id}  {count}" for priority, initiative_id, count in entries]
