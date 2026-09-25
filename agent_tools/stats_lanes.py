"""Busy lanes per clock hour, from the store's run spans. Pure: the clock and the store arrive as arguments."""

from __future__ import annotations

import datetime
import itertools
from collections.abc import Sequence
from typing import Any

Span = tuple[str, str, str | None]
_HOUR = datetime.timedelta(hours=1)


def _parse(stamp: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(stamp).astimezone(datetime.UTC)


def _floor_hour(t: datetime.datetime) -> datetime.datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def window_start(now: datetime.datetime, hours: int) -> datetime.datetime:
    """The first hour of the window: the last `hours` whole clock hours, the final one cut off at `now`."""
    top = _floor_hour(now)
    return (top if top == now else top + _HOUR) - hours * _HOUR


def _hour_row(intervals: Sequence[tuple[datetime.datetime, datetime.datetime]], lo: datetime.datetime, hi: datetime.datetime) -> dict[str, Any]:
    clipped = [(max(s, lo), min(e, hi)) for s, e in intervals]
    live = [(s, e) for s, e in clipped if s < e]
    points = sorted({lo, hi, *(t for pair in live for t in pair)})
    segments = [(b - a).total_seconds() for a, b in itertools.pairwise(points)]
    levels = [sum(1 for s, e in live if s <= a < e) for a in points[:-1]]
    covered = (hi - lo).total_seconds()
    return {
        "hour": lo.isoformat(),
        "avg_busy": round(sum(d * n for d, n in zip(segments, levels, strict=True)) / covered, 2),
        "peak": max(levels),
        "idle_min": round(sum(d for d, n in zip(segments, levels, strict=True) if n == 0) / 60),
    }


def lanes_by_hour(spans: Sequence[Span], now: datetime.datetime, hours: int) -> list[dict[str, Any]]:
    """One row per hour of `window_start(now, hours)` on. A span with no end counts to `now`, so a crashed run whose row never closed reads as busy."""
    now_utc = now.astimezone(datetime.UTC)
    intervals = [(_parse(launched), now_utc if ended is None else _parse(ended)) for _id, launched, ended in spans]
    first = window_start(now_utc, hours)
    return [
        _hour_row(intervals, lo, min(lo + _HOUR, now_utc))
        for lo in (first + i * _HOUR for i in range(hours))
    ]


def totals(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The mean of the hourly averages (the cut-off last hour counts as a whole one), the highest peak, the summed idle minutes."""
    return {
        "avg_busy": round(sum(r["avg_busy"] for r in rows) / len(rows), 2) if rows else 0.0,
        "peak": max((r["peak"] for r in rows), default=0),
        "idle_min": sum(r["idle_min"] for r in rows),
    }


def render_totals(rows: Sequence[dict[str, Any]]) -> str:
    t = totals(rows)
    return f"lanes: avg {t['avg_busy']:.1f} busy, peak {t['peak']}, idle {t['idle_min']} min over {len(rows)} h"


def render_table(rows: Sequence[dict[str, Any]], tz: datetime.tzinfo | None) -> str:
    """The hour reads as local HH:00; `tz=None` is the machine's zone."""
    head = f"{'hour':<6}{'avg busy':>10}{'peak':>6}{'idle min':>10}"
    body = [
        f"{datetime.datetime.fromisoformat(r['hour']).astimezone(tz):%H}:00 {r['avg_busy']:>10.2f}{r['peak']:>6}{r['idle_min']:>10}"
        for r in rows
    ]
    return "\n".join([head, *body])
