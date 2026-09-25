"""Per-role model tiers: summaries, a cost-aware pick, and the spend it would save."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

# A gap of exactly 0.05 can subtract to 0.05000000000000004; the boundary is inclusive.
_EPSILON = 1e-9


@dataclass(frozen=True)
class TierRow:
    """One call; `task_landed` is a task-level flag, so every call of a task carries the same value."""

    role: str
    model: str
    task_id: str
    cost_usd: float
    turns: int
    attempt: int
    task_landed: bool


@dataclass(frozen=True)
class ModelSummary:
    role: str
    model: str
    calls: int
    tasks: int
    cost_per_call: float
    avg_turns: float
    landed_rate: float
    first_try_rate: float | None
    cost_per_landed: float | None


@dataclass(frozen=True)
class Recommendation:
    """`task_counts` covers every model in the role, eligible or not."""

    role: str
    pick: str | None
    best: str | None
    pick_cost_per_call: float | None
    enough_evidence: bool
    task_counts: tuple[tuple[str, int], ...]


def _summary(role: str, model: str, rows: Sequence[TierRow], retried: frozenset[tuple[str, str]]) -> ModelSummary:
    tasks = {r.task_id for r in rows}
    landed = {r.task_id for r in rows if r.task_landed}
    first_try = {t for t in landed if (role, t) not in retried}
    cost = sum(r.cost_usd for r in rows)
    return ModelSummary(
        role=role,
        model=model,
        calls=len(rows),
        tasks=len(tasks),
        cost_per_call=cost / len(rows),
        avg_turns=sum(r.turns for r in rows) / len(rows),
        landed_rate=len(landed) / len(tasks),
        first_try_rate=len(first_try) / len(tasks) if role == "build" else None,
        cost_per_landed=cost / len(landed) if landed else None,
    )


def summarise(rows: Iterable[TierRow]) -> tuple[ModelSummary, ...]:
    """One summary per role and model, sorted by role then model."""
    ordered = sorted(rows, key=lambda r: (r.role, r.model))
    # A task retried on any model of its role is not a first-try landing for the model that made attempt 1.
    retried = frozenset((r.role, r.task_id) for r in ordered if r.attempt > 1)
    groups = itertools.groupby(ordered, key=lambda r: (r.role, r.model))
    return tuple(_summary(role, model, list(group), retried) for (role, model), group in groups)


def _recommendation(role: str, models: Sequence[ModelSummary], min_samples: int, tolerance: float) -> Recommendation:
    counts = tuple((m.model, m.tasks) for m in models)
    eligible = [m for m in models if m.tasks >= min_samples]
    if len(eligible) < 2:
        return Recommendation(role, None, None, None, False, counts)
    top = max(m.landed_rate for m in eligible)
    best = min((m for m in eligible if m.landed_rate == top), key=lambda m: m.model)
    close = [m for m in eligible if top - m.landed_rate <= tolerance + _EPSILON]
    pick = min(close, key=lambda m: (m.cost_per_call, -m.landed_rate, m.model))
    return Recommendation(role, pick.model, best.model, pick.cost_per_call, True, counts)


def recommend(
    summaries: Iterable[ModelSummary], min_samples: int, tolerance: float = 0.05
) -> tuple[Recommendation, ...]:
    """One recommendation per role, sorted by role."""
    ordered = sorted(summaries, key=lambda s: (s.role, s.model))
    groups = itertools.groupby(ordered, key=lambda s: s.role)
    return tuple(_recommendation(role, list(group), min_samples, tolerance) for role, group in groups)


def _saving(rec: Recommendation, calls: Sequence[TierRow]) -> float:
    if rec.pick_cost_per_call is None or not calls:
        return 0.0
    return len(calls) * (sum(r.cost_usd for r in calls) / len(calls) - rec.pick_cost_per_call)


def savings(rows: Iterable[TierRow], recommendations: Iterable[Recommendation]) -> dict[str, float]:
    """Signed: calls times actual minus recommended cost per call, per role; 0.0 without a pick."""
    kept = list(rows)
    return {rec.role: _saving(rec, [r for r in kept if r.role == rec.role]) for rec in recommendations}


def _cell(value: float | None, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def _model_line(s: ModelSummary) -> str:
    return (
        f"  {s.model}: calls {s.calls}, tasks {s.tasks}, ${s.cost_per_call:.4f}/call, turns {s.avg_turns:.1f}, "
        f"landed {s.landed_rate:.0%}, first-try {_cell(s.first_try_rate, '.0%')}, ${_cell(s.cost_per_landed, '.4f')}/landed"
    )


def _verdict_line(rec: Recommendation) -> str:
    if rec.pick is None:
        return "  not enough evidence (" + ", ".join(f"{m}: {n} tasks" for m, n in rec.task_counts) + ")"
    return f"  pick: {rec.pick} (best landed rate: {rec.best})"


def render_lines(
    summaries: Sequence[ModelSummary], recommendations: Sequence[Recommendation], saved: dict[str, float]
) -> list[str]:
    return [
        line
        for rec in recommendations
        for line in (
            rec.role,
            *(_model_line(s) for s in summaries if s.role == rec.role),
            _verdict_line(rec),
            f"  savings: {saved.get(rec.role, 0.0):+.4f} USD",
        )
    ]


def to_json(
    summaries: Sequence[ModelSummary], recommendations: Sequence[Recommendation], saved: dict[str, float]
) -> dict[str, Any]:
    return {
        "summaries": [asdict(s) for s in summaries],
        "recommendations": [
            {**asdict(r), "task_counts": [{"model": m, "tasks": n} for m, n in r.task_counts]} for r in recommendations
        ],
        "savings": dict(saved),
    }
