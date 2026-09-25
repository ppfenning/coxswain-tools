"""Pure core for `cox stats causes`: quarantined attempts grouped by cause, with the kind breakdown and sample reasons.

An attempt with no recorded cause is `unrecorded`. It is never `unknown`, because graphs uses `unknown` as a real class."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

UNRECORDED = "unrecorded"
SAMPLE_CAP = 3


@dataclass(frozen=True)
class CauseRow:
    cause: str
    count: int
    share: float  # of all attempts read, 0..1
    kinds: tuple[tuple[str, int], ...]  # by count descending, then name
    samples: tuple[str, ...]  # `cause_why`, else `reason`; blanks dropped, repeats dropped, first seen first


def _text(value: Any) -> str:
    return (value or "").strip()


def _cause(row: Mapping[str, Any]) -> str:
    return _text(row.get("cause")) or UNRECORDED


def _kind(row: Mapping[str, Any]) -> str:
    return _text(row.get("kind")) or UNRECORDED


def _sample(row: Mapping[str, Any]) -> str:
    return _text(row.get("cause_why")) or _text(row.get("reason"))


def _ranked(counts: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def summarise(rows: Sequence[Mapping[str, Any]], sample_cap: int = SAMPLE_CAP) -> list[CauseRow]:
    """One row per cause, biggest first then by name. `rows` keep their given order, which fixes which samples come first."""
    total = len(rows)
    groups = {c: [r for r in rows if _cause(r) == c] for c in sorted({_cause(r) for r in rows})}
    summaries = [
        CauseRow(
            cause=cause,
            count=len(group),
            share=len(group) / total,
            kinds=_ranked(Counter(_kind(r) for r in group)),
            samples=tuple(dict.fromkeys(s for s in map(_sample, group) if s))[:sample_cap],
        )
        for cause, group in groups.items()
    ]
    return sorted(summaries, key=lambda s: (-s.count, s.cause))


def render_lines(summaries: Sequence[CauseRow]) -> list[str]:
    if not summaries:
        return ["no quarantined attempts"]
    total = sum(s.count for s in summaries)
    return [
        f"{total} quarantined attempts",
        *(
            line
            for s in summaries
            for line in (
                f"{s.cause}  {s.count}  {s.share:.0%}",
                "  kinds: " + ", ".join(f"{k} {n}" for k, n in s.kinds),
                *(f"  - {' '.join(sample.split())}" for sample in s.samples),
            )
        ),
    ]


def to_json(summaries: Sequence[CauseRow]) -> dict[str, Any]:
    return {
        "total": sum(s.count for s in summaries),
        "causes": [
            {"cause": s.cause, "count": s.count, "share": round(s.share, 4), "kinds": dict(s.kinds), "samples": list(s.samples)}
            for s in summaries
        ],
    }
