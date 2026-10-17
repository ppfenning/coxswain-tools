import sqlite3
from datetime import UTC, datetime

from agent_tools.chair_land_freeze import (
    LandFreeze,
    UsageError,
    active_freeze,
    expiry_after,
    freeze_text,
    read_freeze,
    run_freeze,
    status_text,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 26, 12, 30, tzinfo=UTC)
FROZEN = "lands frozen until 12:30 (release cut)"
NOT_FROZEN = "lands are not frozen"


def test_a_live_row_is_a_freeze():
    row = {"holder": "release cut", "expires_at": "2026-09-26T12:30:00Z"}
    assert active_freeze(row, NOW) == LandFreeze("release cut", LATER)


def test_an_expired_row_is_no_freeze():
    assert active_freeze({"holder": "release cut", "expires_at": "2026-09-26T11:59:00Z"}, NOW) is None


def test_a_row_expiring_exactly_now_is_no_freeze():
    assert active_freeze({"holder": "release cut", "expires_at": "2026-09-26T12:00:00Z"}, NOW) is None


def test_no_row_or_an_unparseable_expiry_is_no_freeze():
    assert active_freeze(None, NOW) is None
    assert active_freeze({"holder": "x", "expires_at": "soon"}, NOW) is None


def test_the_status_text_names_the_expiry_and_reason():
    assert freeze_text(LandFreeze("release cut", LATER)) == FROZEN
    assert status_text(LandFreeze("release cut", LATER)) == FROZEN


def test_the_no_freeze_text_is_exact():
    assert status_text(None) == NOT_FROZEN


def test_expiry_adds_minutes_and_rejects_below_one_as_a_value():
    assert expiry_after(NOW, 30) == LATER
    assert isinstance(expiry_after(NOW, 0), UsageError)
    assert isinstance(expiry_after(NOW, -5), UsageError)


def _store(runs_dir, *rows):
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute("CREATE TABLE leases (name TEXT PRIMARY KEY, holder TEXT, epoch INTEGER, heartbeat_at TEXT, expires_at TEXT)")
    conn.executemany("INSERT INTO leases VALUES (?, ?, ?, '2026-09-26T11:59:00Z', ?)", rows)
    conn.commit()
    conn.close()


def _names(runs_dir):
    conn = sqlite3.connect(runs_dir / "cox.db")
    try:
        return sorted(r[0] for r in conn.execute("SELECT name FROM leases"))
    finally:
        conn.close()


def test_set_writes_the_row_and_reads_back_and_replaces_an_older_one(tmp_path):
    _store(tmp_path, ("land-freeze", "old", 0, "2026-09-26T18:00:00Z"), ("chair", "s@h:7", 4, "2026-09-26T12:05:00Z"))
    assert run_freeze(tmp_path, NOW, 30, "release cut", False) == FROZEN
    assert read_freeze(tmp_path, NOW) == LandFreeze("release cut", LATER)
    assert _names(tmp_path) == ["chair", "land-freeze"]


def test_off_deletes_the_row_and_leaves_other_leases(tmp_path):
    _store(tmp_path, ("land-freeze", "release cut", 0, "2026-09-26T12:30:00Z"), ("chair", "s@h:7", 4, "2026-09-26T12:05:00Z"))
    assert run_freeze(tmp_path, NOW, None, None, True) == NOT_FROZEN
    assert _names(tmp_path) == ["chair"]


def test_no_flag_prints_the_status(tmp_path):
    _store(tmp_path)
    assert run_freeze(tmp_path, NOW, None, None, False) == NOT_FROZEN
    run_freeze(tmp_path, NOW, 30, "release cut", False)
    assert run_freeze(tmp_path, NOW, None, None, False) == FROZEN


def test_no_flag_with_an_expired_row_prints_not_frozen(tmp_path):
    _store(tmp_path, ("land-freeze", "release cut", 0, "2026-09-26T12:00:00Z"))
    assert run_freeze(tmp_path, NOW, None, None, False) == NOT_FROZEN


def test_a_missing_part_of_set_is_a_usage_error_and_writes_nothing(tmp_path):
    _store(tmp_path)
    assert run_freeze(tmp_path, NOW, 30, None, False).startswith("usage:")
    assert run_freeze(tmp_path, NOW, None, "release cut", False).startswith("usage:")
    assert run_freeze(tmp_path, NOW, 30, "  ", False).startswith("usage:")
    assert run_freeze(tmp_path, NOW, 30, "release cut", True).startswith("usage:")
    assert run_freeze(tmp_path, NOW, 0, "release cut", False).startswith("MINUTES must be at least 1")
    assert _names(tmp_path) == []


def test_a_missing_store_reports_a_failure_and_creates_nothing(tmp_path):
    assert run_freeze(tmp_path, NOW, 30, "release cut", False).startswith("could not write")
    assert run_freeze(tmp_path, NOW, None, None, True).startswith("could not clear")
    assert run_freeze(tmp_path, NOW, None, None, False) == NOT_FROZEN
    assert not (tmp_path / "cox.db").exists()
