"""Pure boat-race rendering for `cox home btop`: one lane per run, position derived from node progress. No curses, no clock."""

from __future__ import annotations

from agent_tools.home_model import Line, Span
from agent_tools.runs_detail import NODE_ORDER

__all__ = ["SPRITES", "STATUS_ROLE", "lane", "progress_of", "regatta"]

SPRITES = ("<--<", "<~~<")

STATUS_ROLE = {
    "running": "ok",
    "orphaned": "alert",
    "quarantined": "alert",
    "budget": "warn",
    "stalled": "warn",
    "exited": "dim",
}

_FINISH = (Span("finish", "title"),)


def progress_of(row) -> float:
    if row.node not in NODE_ORDER:
        return 0.0
    return min(1.0, max(0.0, NODE_ORDER.index(row.node) / (len(NODE_ORDER) - 1)))


def lane(name: str, progress: float, status: str, width: int, tick: int) -> Line:
    hull = SPRITES[0] if status == "exited" else SPRITES[tick % len(SPRITES)]
    hull_width = len(hull)
    clamped = min(1.0, max(0.0, progress))
    pos = round(clamped * (width - hull_width))
    role = STATUS_ROLE.get(status, "plain")
    before = pos
    after = width - hull_width - pos
    spans = []
    if before:
        spans.append(Span("~" * before, "dim"))
    spans.append(Span(hull, role))
    if after:
        spans.append(Span("~" * after, "dim"))
    return tuple(spans)


def regatta(rows, width: int, tick: int) -> tuple[Line, ...]:
    lanes = tuple(lane(row.run, progress_of(row), row.status, width, tick) for row in rows)
    return (_FINISH, *lanes)
