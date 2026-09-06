"""The pure pacing planner: given a spend window and a policy, what a run may
spend next. No clock, network or filesystem here — the edge module in a
later phase gathers the real window (from ccusage, a run log) and hands it
in as plain data; `now` is always supplied by the caller.

The provider profile is a ceiling, never a setting: `assess` only ever
tightens `tier_ceiling`/`effort_ceiling` down a ladder read from `policy`,
and never loosens them back up within one call. `stop` outranks `hold`: a
pace ratio that has climbed past the last rung either ladder can offer
returns `stop` before headroom is even considered, so a temporary spike in
`burn_usd_per_hour` can never mask the harsher pace verdict as the softer
one. The last combined rung (cheapest tier, lowest effort) is still
`go_degraded`; `stop` only fires one threshold beyond that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = ["Assessment", "Policy", "Window", "assess"]


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    spent_usd: float
    ceiling_usd: float | None
    burn_usd_per_hour: float
    runs_in_flight: int  # carried for a future concurrency-aware ladder; assess does not read it yet


@dataclass(frozen=True)
class Policy:
    # `pace_thresholds` wants one entry per rung of the combined ladder --
    # (len(tier_ladder) - 1) + (len(effort_ladder) - 1) entries -- to reach
    # the fully degraded rung (cheapest tier, lowest effort) as
    # `go_degraded`, plus one more threshold beyond that for `stop` to be
    # reachable by pace alone. Fewer thresholds than the ladder's own step
    # count just means the ladder never fully degrades by pace; the module
    # never assumes the two counts agree.
    pace_thresholds: tuple[float, ...]
    tier_ladder: tuple[str, ...]
    effort_ladder: tuple[str, ...]
    min_headroom_usd: float


@dataclass(frozen=True)
class Assessment:
    spent_fraction: float | None
    elapsed_fraction: float
    projected_total: float
    headroom_usd: float | None
    verdict: str
    tier_ceiling: str
    effort_ceiling: str
    hold_until: datetime | None
    reason: str


def _elapsed_fraction(window: Window, now: datetime) -> float:
    span = (window.end - window.start).total_seconds()
    if span <= 0:
        return 1.0 if now >= window.end else 0.0
    return min(1.0, max(0.0, (now - window.start).total_seconds() / span))


def _projected_total(window: Window, now: datetime) -> float:
    hours_remaining = max(0.0, (window.end - now).total_seconds() / 3600)
    return window.spent_usd + window.burn_usd_per_hour * hours_remaining


def _ratio(spent_fraction: float, elapsed_fraction: float) -> float:
    """Pace: how far ahead of the clock the spend is running."""
    if elapsed_fraction > 0:
        return spent_fraction / elapsed_fraction
    if spent_fraction > 0:
        return float("inf")
    return 0.0


def _rung(ratio: float, thresholds: tuple[float, ...]) -> int:
    return sum(1 for t in thresholds if ratio > t)


def _ladder_ceilings(rung: int, policy: Policy) -> tuple[str, str]:
    """Walk `rung` steps down tier first, then effort, never past either
    ladder's last entry — the cheapest rung both ladders can reach."""
    tier_steps = len(policy.tier_ladder) - 1
    tier_idx = min(rung, tier_steps)
    effort_idx = min(max(rung - tier_steps, 0), len(policy.effort_ladder) - 1)
    return policy.tier_ladder[tier_idx], policy.effort_ladder[effort_idx]


def _unmeasured(window: Window, policy: Policy, elapsed_fraction: float, now: datetime) -> Assessment:
    return Assessment(
        spent_fraction=None,
        elapsed_fraction=elapsed_fraction,
        projected_total=_projected_total(window, now),
        headroom_usd=None,
        verdict="go",
        tier_ceiling=policy.tier_ladder[0],
        effort_ceiling=policy.effort_ladder[0],
        hold_until=None,
        reason="window is unmeasured: no usable ceiling_usd; reporting pace only",
    )


def assess(window: Window, policy: Policy, now: datetime) -> Assessment:
    """Pure: the one verdict a window and a policy make at `now`. Reports an
    unmeasured window rather than guessing a ceiling for it."""
    elapsed_fraction = _elapsed_fraction(window, now)
    if window.ceiling_usd is None or window.ceiling_usd <= 0:
        return _unmeasured(window, policy, elapsed_fraction, now)

    spent_fraction = window.spent_usd / window.ceiling_usd
    projected_total = _projected_total(window, now)
    headroom_usd = window.ceiling_usd - projected_total
    ratio = _ratio(spent_fraction, elapsed_fraction)
    # The last combined ladder index (cheapest tier, lowest effort) is still
    # a degraded rung, not a stop; stop needs one threshold past it.
    max_rung = (len(policy.tier_ladder) - 1) + (len(policy.effort_ladder) - 1)
    rung = _rung(ratio, policy.pace_thresholds)
    pace = f"spent {spent_fraction:.0%} of ceiling at {elapsed_fraction:.0%} elapsed"

    if rung > max_rung:
        return Assessment(
            spent_fraction=spent_fraction, elapsed_fraction=elapsed_fraction,
            projected_total=projected_total, headroom_usd=headroom_usd,
            verdict="stop", tier_ceiling=policy.tier_ladder[-1], effort_ceiling=policy.effort_ladder[-1],
            hold_until=None, reason=f"{pace}; both ladders exhausted",
        )

    tier_ceiling, effort_ceiling = _ladder_ceilings(rung, policy)

    if headroom_usd < policy.min_headroom_usd:
        return Assessment(
            spent_fraction=spent_fraction, elapsed_fraction=elapsed_fraction,
            projected_total=projected_total, headroom_usd=headroom_usd,
            verdict="hold", tier_ceiling=policy.tier_ladder[-1], effort_ceiling=policy.effort_ladder[-1],
            hold_until=window.end,
            reason=f"headroom ${headroom_usd:.2f} below minimum ${policy.min_headroom_usd:.2f}; holding until the window ends",
        )

    if rung == 0:
        return Assessment(
            spent_fraction=spent_fraction, elapsed_fraction=elapsed_fraction,
            projected_total=projected_total, headroom_usd=headroom_usd,
            verdict="go", tier_ceiling=tier_ceiling, effort_ceiling=effort_ceiling,
            hold_until=None, reason=f"{pace}; on pace",
        )

    return Assessment(
        spent_fraction=spent_fraction, elapsed_fraction=elapsed_fraction,
        projected_total=projected_total, headroom_usd=headroom_usd,
        verdict="go_degraded", tier_ceiling=tier_ceiling, effort_ceiling=effort_ceiling,
        hold_until=None, reason=f"{pace}; degrading to tier={tier_ceiling} effort={effort_ceiling}",
    )
