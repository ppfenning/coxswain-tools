from datetime import UTC, datetime

from agent_tools.chair_holder import HolderLease, HolderVerdict, chair_holder_verdict, format_holder_verdict, tick_age_s

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)
TICK = datetime(2026, 10, 14, 11, 59, 30, tzinfo=UTC)
FUTURE = datetime(2026, 10, 14, 13, 0, 0, tzinfo=UTC)
PAST = datetime(2026, 10, 14, 11, 0, 0, tzinfo=UTC)
LOOP = HolderLease("chair-run", 4242, "alpha", FUTURE, True)


def test_live_same_host():
    v = chair_holder_verdict(LOOP, "alpha", NOW, TICK, True, 120.0)
    assert v == HolderVerdict("live_same_host", "chair-run", 4242, "alpha", 30.0, "")


def test_live_other_host():
    v = chair_holder_verdict(LOOP, "beta", NOW, TICK, None, 120.0)
    assert v == HolderVerdict("live_other_host", "chair-run", 4242, "alpha", 30.0, "")


def test_other_host_stale_tick():
    v = chair_holder_verdict(LOOP, "beta", NOW, TICK, None, 10.0)
    assert (v.kind, v.reason) == ("none", "stale tick")


def test_expired():
    lease = HolderLease("chair-run", 4242, "alpha", PAST, True)
    v = chair_holder_verdict(lease, "alpha", NOW, TICK, True, 120.0)
    assert (v.kind, v.reason) == ("none", "expired")


def test_dead_pid():
    v = chair_holder_verdict(LOOP, "alpha", NOW, TICK, False, 120.0)
    assert (v.kind, v.reason) == ("none", "dead")


def test_session_style_holder():
    lease = HolderLease("claude-session", 77, "alpha", FUTURE, False)
    v = chair_holder_verdict(lease, "alpha", NOW, TICK, True, 120.0)
    assert (v.kind, v.reason) == ("none", "not a loop")


def test_tick_age_s():
    assert tick_age_s(NOW, TICK) == 30.0
    assert tick_age_s(NOW, None) is None


def test_format_is_one_line():
    line = format_holder_verdict(HolderVerdict("live_other_host", "chair-run", 4242, "alpha", 30.0))
    assert line == "chair loop chair-run pid 4242 on alpha, last tick 30s ago"
    assert "\n" not in line
