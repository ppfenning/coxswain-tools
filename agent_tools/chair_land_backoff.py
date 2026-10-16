"""Pure backoff for a refused land, keyed by (initiative, phase, run)."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import NotRequired, TypedDict

BASE = timedelta(minutes=15)
CAP = timedelta(hours=2)


class LandRefusal(TypedDict):
    initiative: str
    phase: str
    run: str
    attempts: int
    last_refused_at: str  # ISO UTC
    head: NotRequired[str]  # phase branch head sha at refusal; absent means none was recorded


# 2 ** 3 * BASE already reaches CAP; a larger exponent only risks the timedelta overflow a run with
# 1267 recorded refusals produced on 2026-10-11 (every tick died in planning, before any action).
_MAX_DOUBLINGS = 3


def backoff_for(attempts: int) -> timedelta:
    """15 minutes for the first refusal, doubling per attempt, never more than 2 hours; any attempt count is safe."""
    return min(CAP, BASE * 2 ** min(_MAX_DOUBLINGS, max(0, attempts - 1)))


def aligned(stamp: str, now: datetime) -> datetime:
    """The ISO stamp in `now`'s awareness; a naive stamp is UTC, so naive and aware never meet in a comparison."""
    parsed = datetime.fromisoformat(stamp)
    if now.tzinfo is None:
        return parsed if parsed.tzinfo is None else parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def next_attempt_at(refusal: LandRefusal) -> datetime:
    return datetime.fromisoformat(refusal["last_refused_at"]) + backoff_for(refusal["attempts"])


def suppressed(refusal: LandRefusal, now: datetime, current_head: str | None = None) -> bool:
    """A row missing its stamp suppresses nothing; a missing attempt count backs off as one attempt.

    An open window is released when the row recorded a head and `current_head` is a different one.
    """
    stamp = refusal.get("last_refused_at", "")
    recorded = refusal.get("head")
    moved = bool(recorded) and current_head is not None and recorded != current_head
    return bool(stamp) and not moved and now < aligned(stamp, now) + backoff_for(refusal.get("attempts", 1))


def suppressing(
    refusals: Iterable[LandRefusal],
    initiative: str,
    phase: str,
    run: str,
    now: datetime,
    current_head: str | None = None,
) -> LandRefusal | None:
    """The refusal still backing off this exact run at this head; a new run id of the phase matches none."""
    return next(
        (
            r
            for r in refusals
            if (r.get("initiative"), r.get("phase"), r.get("run")) == (initiative, phase, run)
            and suppressed(r, now, current_head)
        ),
        None,
    )
