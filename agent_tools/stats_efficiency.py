"""Pure core for `cox stats efficiency`: per-UTC-day cost, landed tasks, first-try rate, waste and cache share.

A task is its work-item task_id across every run that tried it. Every ratio is None when its denominator is zero."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

TOTAL = "total"
IN_FLIGHT = timedelta(days=2)


@dataclass(frozen=True)
class EfficiencyRow:
    day: str
    cost_usd: float
    turns: int
    cost_per_turn: float | None
    landed: int
    cost_per_landed: float | None
    first_try_rate: float | None  # landed tasks with exactly one build call over all runs, over landed tasks
    waste_share: float | None  # task-scoped spend on tasks that never landed, over task-scoped spend
    cache_read_share: float | None  # cache_read_tokens over input_total


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def _land(line: str) -> tuple[str, str] | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if isinstance(row, dict) and row.get("exit") == 0 and "mark_done" in (row.get("steps_reached") or []) and row.get("task"):
        ts = row.get("ts")
        return (ts[:10] if isinstance(ts, str) else "", str(row["task"]))
    return None


def landed_lands(lines: Iterable[str]) -> list[tuple[str, str]]:
    """(day, task_id) per `land.jsonl` line with exit 0, `mark_done` reached and a task; a line with no `ts` has day ''."""
    return [land for land in map(_land, lines) if land is not None]


def first_land_days(lands: Iterable[tuple[str, str]]) -> dict[str, str]:
    """task_id -> the earliest dated day it landed. A task landed twice counts once, on its first day."""
    dated = sorted((day, task) for day, task in lands if day)
    return {task: day for day, task in reversed(dated)}


def _is_in_flight(last_ts: str | None, now: datetime) -> bool:
    """True when the last call is under two days before `now`. A naive stamp reads as UTC; an unparseable one as old."""
    try:
        last = datetime.fromisoformat(last_ts or "")
    except ValueError:
        return False
    return now - last.replace(tzinfo=last.tzinfo or UTC) < IN_FLIGHT


def _row(
    day: str,
    calls: Sequence[Mapping[str, Any]],
    landed: frozenset[str],
    ever_landed: frozenset[str],
    tasks: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> EfficiencyRow:
    cost = sum(c["cost_usd"] for c in calls)
    turns = sum(c["turns"] for c in calls)
    scoped = [c for c in calls if c["task_id"] is not None and not _is_in_flight(tasks.get(c["task_id"], {}).get("last_ts"), now)]
    wasted = sum(c["cost_usd"] for c in scoped if c["task_id"] not in ever_landed)
    first_try = sum(1 for t in landed if tasks.get(t, {}).get("builds") == 1)
    return EfficiencyRow(
        day=day,
        cost_usd=cost,
        turns=turns,
        cost_per_turn=_ratio(cost, turns),
        landed=len(landed),
        cost_per_landed=_ratio(cost, len(landed)),
        first_try_rate=_ratio(first_try, len(landed)),
        waste_share=_ratio(wasted, sum(c["cost_usd"] for c in scoped)),
        cache_read_share=_ratio(sum(c["cache_read_tokens"] for c in calls), sum(c["input_total"] for c in calls)),
    )


def efficiency(
    calls: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
    lands: Sequence[tuple[str, str]],
    since: str,
    now: datetime,
) -> tuple[list[EfficiencyRow], EfficiencyRow]:
    """Per-day rows oldest first, and a total whose landed count is the sum of the days'.

    `calls` are per (day, task_id) sums, `tasks` per task_id build counts and last call ts. A task's land counts on its
    first land day when that day is on or after `since`; a land at any time keeps the task's spend out of waste."""
    by_task = {t["task_id"]: t for t in tasks}
    ever_landed = frozenset(task for _, task in lands)
    counted = {task: day for task, day in first_land_days(lands).items() if day >= since}
    days = sorted({c["day"] for c in calls} | set(counted.values()))
    rows = [
        _row(
            day,
            [c for c in calls if c["day"] == day],
            frozenset(task for task, d in counted.items() if d == day),
            ever_landed, by_task, now,
        )
        for day in days
    ]
    return rows, _row(TOTAL, calls, frozenset(counted), ever_landed, by_task, now)


def _cell(value: float | None, fmt: str) -> str:
    return "-" if value is None else format(value, fmt)


def render_lines(rows: Sequence[EfficiencyRow], total: EfficiencyRow) -> list[str]:
    head = ["day", "cost", "turns", "$/turn", "landed", "$/landed", "first-try", "waste", "cache-read"]
    body = [
        [
            r.day, f"{r.cost_usd:.2f}", str(r.turns), _cell(r.cost_per_turn, ".3f"), str(r.landed),
            _cell(r.cost_per_landed, ".2f"), _cell(r.first_try_rate, ".0%"), _cell(r.waste_share, ".0%"), _cell(r.cache_read_share, ".0%"),
        ]
        for r in [*rows, total]
    ]
    widths = [max(len(line[i]) for line in [head, *body]) for i in range(len(head))]
    return ["  ".join(cell.ljust(w) for cell, w in zip(line, widths, strict=True)).rstrip() for line in [head, *body]]


def to_json(rows: Sequence[EfficiencyRow], total: EfficiencyRow) -> dict[str, Any]:
    return {"days": [asdict(r) for r in rows], "total": asdict(total)}
