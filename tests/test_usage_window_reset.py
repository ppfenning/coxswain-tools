import sqlite3
from datetime import UTC, datetime, timedelta, timezone

from agent_tools import usage_window
from agent_tools.pacing import Window
from agent_tools.usage_window import gather_weekly, weekly_start_from_reset, weekly_window_from

_NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
_WEEK = timedelta(days=7)
_PLUS_TWO = timezone(timedelta(hours=2))


def test_start_is_resets_at_minus_seven_days_when_the_reset_is_three_days_ahead():
    assert weekly_start_from_reset(_NOW + timedelta(days=3), None, _NOW) == _NOW - timedelta(days=4)


def test_gather_weekly_with_a_start_excludes_spend_before_it_and_includes_spend_after_it(tmp_path):
    start = weekly_start_from_reset(_NOW + timedelta(days=3), None, _NOW)
    usage = [
        (start - timedelta(hours=1), {"cost_usd": 100.0}),
        (start + timedelta(hours=1), {"cost_usd": 7.0}),
    ]
    window = gather_weekly(tmp_path, _NOW, usage=usage, start=start)
    assert (window.start, window.spent_usd, window.runs_in_flight) == (start, 7.0, 1)


def test_gather_weekly_with_a_start_hands_it_to_the_store(tmp_path):
    start = _NOW - timedelta(days=4)
    seen: list[str] = []
    window = gather_weekly(tmp_path, _NOW, store_spend=lambda since: seen.append(since) or 5.0, start=start)
    assert (seen, window.start, window.spent_usd) == ([start.isoformat()], start, 5.0)


def test_a_past_resets_at_rolls_forward_by_whole_weeks():
    resets_at = _NOW - timedelta(days=10)
    start = weekly_start_from_reset(resets_at, None, _NOW)
    assert start == resets_at - _WEEK + 2 * _WEEK
    assert start <= _NOW < start + _WEEK


def test_a_past_configured_reset_rolls_forward_by_whole_weeks():
    configured = _NOW - timedelta(days=15)
    assert weekly_start_from_reset(None, configured, _NOW) == configured + 2 * _WEEK


def test_a_configured_reset_in_the_future_rolls_back_by_whole_weeks():
    configured = _NOW + timedelta(days=2)
    assert weekly_start_from_reset(None, configured, _NOW) == configured - _WEEK


def test_resets_at_seven_days_ahead_starts_the_window_now():
    assert weekly_start_from_reset(_NOW + _WEEK, None, _NOW) == _NOW


def test_resets_at_wins_over_the_configured_reset():
    configured = _NOW - timedelta(days=1)
    assert weekly_start_from_reset(_NOW + timedelta(days=3), configured, _NOW) == _NOW - timedelta(days=4)


def test_with_no_reset_input_the_start_is_now_minus_seven_days():
    assert weekly_start_from_reset(None, None, _NOW) == _NOW - _WEEK


def test_gather_weekly_without_a_start_keeps_the_rolling_window(tmp_path):
    usage = [
        (_NOW - timedelta(days=8), {"cost_usd": 50.0}),
        (_NOW - timedelta(days=3), {"cost_usd": 4.0}),
    ]
    expected = Window(
        start=_NOW - _WEEK, end=_NOW, spent_usd=4.0, ceiling_usd=40.0,
        burn_usd_per_hour=4.0 / (7 * 24), runs_in_flight=1,
    )
    assert gather_weekly(tmp_path, _NOW, weekly_ceiling_usd=40.0, usage=usage) == expected
    assert weekly_window_from(usage, _NOW, 40.0) == expected


def test_gather_weekly_without_a_start_asks_the_store_from_the_rolling_start(tmp_path):
    seen: list[str] = []
    window = gather_weekly(tmp_path, _NOW, store_spend=lambda since: seen.append(since) or 9.0)
    assert (seen, window.start, window.spent_usd) == ([(_NOW - _WEEK).isoformat()], _NOW - _WEEK, 9.0)


_CALL_COLUMNS = (
    "call_id", "run_id", "seq", "ok", "model_alias", "decision_json", "role", "task_id", "tier", "cost_usd",
    "ceiling_usd", "ceiling_source", "turns", "duration_ms", "input_tokens", "cache_read_tokens",
    "cache_creation_tokens", "input_total", "output_tokens", "ts",
)


def _store_run(runs_dir, run_id, ended_at, cost):
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, launched_at TEXT, ended_at TEXT)")
    conn.execute(f"CREATE TABLE IF NOT EXISTS node_calls ({', '.join(_CALL_COLUMNS)})")
    at = ended_at.isoformat()
    conn.execute("INSERT INTO runs VALUES (?, ?, ?)", (run_id, at, at))
    conn.execute(
        "INSERT INTO node_calls (call_id, run_id, seq, cost_usd, ts, ok) VALUES (?, ?, 1, ?, ?, 1)",
        ("c-" + run_id, run_id, cost, at),
    )
    conn.commit()
    conn.close()


def test_a_plus_two_resets_at_yields_a_start_in_nows_tzinfo():
    start = weekly_start_from_reset(datetime(2026, 10, 14, 14, 0, tzinfo=_PLUS_TWO), None, _NOW)
    assert start.isoformat() == "2026-10-07T12:00:00+00:00"


def test_a_plus_two_resets_at_bounds_the_store_read_of_a_real_runs_dir(tmp_path):
    start = weekly_start_from_reset(datetime(2026, 10, 14, 14, 0, tzinfo=_PLUS_TWO), None, _NOW)
    _store_run(tmp_path, "before", start - timedelta(hours=1), 100.0)
    _store_run(tmp_path, "after", start + timedelta(hours=1), 7.0)
    window = gather_weekly(tmp_path, _NOW, start=start)
    assert (window.start, window.spent_usd, window.runs_in_flight) == (start, 7.0, 1)


def test_gather_weekly_without_a_start_still_reads_through_read_usage(tmp_path, monkeypatch):
    given = [(_NOW - timedelta(days=1), {"cost_usd": 2.0})]
    monkeypatch.setattr(usage_window, "read_usage", lambda runs_dir, now, reset=None: given)
    assert gather_weekly(tmp_path, _NOW).spent_usd == 2.0


def test_gather_weekly_without_a_start_equals_the_old_composition_on_a_real_runs_dir(tmp_path):
    _store_run(tmp_path, "old", _NOW - timedelta(days=8), 50.0)
    _store_run(tmp_path, "new", _NOW - timedelta(days=3), 4.0)
    old = weekly_window_from(usage_window.read_usage(tmp_path, _NOW, None), _NOW, 40.0, None)
    assert gather_weekly(tmp_path, _NOW, weekly_ceiling_usd=40.0) == old
    assert (old.start, old.spent_usd) == (_NOW - _WEEK, 4.0)
