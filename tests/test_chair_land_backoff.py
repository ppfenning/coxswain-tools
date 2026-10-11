from datetime import UTC, datetime, timedelta

from agent_tools.chair_land_backoff import LandRefusal, backoff_for, next_attempt_at, suppressed, suppressing

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _refusal(attempts: int = 1, run: str = "i-1") -> LandRefusal:
    return {"initiative": "i", "phase": "p", "run": run, "attempts": attempts, "last_refused_at": T0.isoformat()}


def test_backoff_doubles_from_fifteen_minutes_and_caps_at_two_hours():
    assert [backoff_for(n) for n in (1, 2, 3, 4, 5)] == [timedelta(minutes=m) for m in (15, 30, 60, 120, 120)]


def test_next_attempt_is_the_last_refusal_plus_the_backoff():
    assert next_attempt_at(_refusal(2)) == T0 + timedelta(minutes=30)


def test_suppressed_inside_the_window_and_released_after_it():
    assert suppressed(_refusal(1), T0 + timedelta(minutes=14))
    assert not suppressed(_refusal(1), T0 + timedelta(minutes=15))


def test_a_different_run_id_is_never_suppressed():
    now = T0 + timedelta(minutes=1)
    assert suppressing([_refusal()], "i", "p", "i-2", now) is None
    assert suppressing([_refusal()], "i", "p", "i-1", now) == _refusal()


def test_backoff_stays_at_the_cap_for_any_attempt_count():
    """1267 recorded refusals once raised OverflowError in planning, killing every tick; the cap must hold for any count."""
    assert backoff_for(1267) == timedelta(hours=2)
    assert backoff_for(10**6) == timedelta(hours=2)
    stamp = datetime(2026, 10, 11, 14, 0, tzinfo=UTC).isoformat()
    refusal = LandRefusal(initiative="i", phase="p", run="i-2", attempts=1267, last_refused_at=stamp)
    assert suppressed(refusal, datetime(2026, 10, 11, 15, 0, tzinfo=UTC))
    assert not suppressed(refusal, datetime(2026, 10, 11, 16, 1, tzinfo=UTC))
