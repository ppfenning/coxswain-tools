import datetime
import json
import sqlite3

from agent_tools import run_store, stats_lanes
from agent_tools.cli import main

UTC = datetime.UTC
NOW = datetime.datetime(2026, 9, 25, 6, 0, tzinfo=UTC)


def at(hour, minute=0):
    return datetime.datetime(2026, 9, 25, hour, minute, tzinfo=UTC).isoformat()


def test_two_overlapping_spans_in_one_hour_give_the_time_weighted_average_and_peak_two():
    spans = [("a", at(4, 0), at(4, 30)), ("b", at(4, 15), at(4, 45))]
    row = stats_lanes.lanes_by_hour(spans, NOW, 2)[0]
    assert row == {"hour": at(4), "avg_busy": 1.0, "peak": 2, "idle_min": 15}


def test_an_hour_with_no_runs_is_sixty_idle_minutes():
    row = stats_lanes.lanes_by_hour([], NOW, 1)[0]
    assert row == {"hour": at(5), "avg_busy": 0.0, "peak": 0, "idle_min": 60}


def test_an_open_span_runs_to_now_and_the_last_hour_is_cut_off_there():
    now = datetime.datetime(2026, 9, 25, 5, 30, tzinfo=UTC)
    rows = stats_lanes.lanes_by_hour([("a", at(4, 30), None)], now, 2)
    assert rows == [
        {"hour": at(4), "avg_busy": 0.5, "peak": 1, "idle_min": 30},
        {"hour": at(5), "avg_busy": 1.0, "peak": 1, "idle_min": 0},
    ]


def test_a_span_that_began_before_the_window_counts_only_inside_it():
    rows = stats_lanes.lanes_by_hour([("a", at(1), at(4, 30))], NOW, 2)
    assert [(r["avg_busy"], r["idle_min"]) for r in rows] == [(0.5, 30), (0.0, 60)]


def test_totals_average_the_hours_take_the_top_peak_and_sum_the_idle_minutes():
    rows = [
        {"hour": at(3), "avg_busy": 2.0, "peak": 3, "idle_min": 10},
        {"hour": at(4), "avg_busy": 4.0, "peak": 6, "idle_min": 32},
    ]
    assert stats_lanes.totals(rows) == {"avg_busy": 3.0, "peak": 6, "idle_min": 42}
    assert stats_lanes.render_totals(rows) == "lanes: avg 3.0 busy, peak 6, idle 42 min over 2 h"
    assert stats_lanes.totals([]) == {"avg_busy": 0.0, "peak": 0, "idle_min": 0}


def test_the_table_shows_each_hour_in_the_given_zone():
    rows = stats_lanes.lanes_by_hour([], NOW, 1)
    plus_two = datetime.timezone(datetime.timedelta(hours=2))
    assert stats_lanes.render_table(rows, plus_two).splitlines()[1].startswith("07:00")


def _store(runs_dir, *rows):
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, launched_at TEXT, ended_at TEXT)")
    conn.executemany("INSERT INTO runs VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_run_spans_keeps_open_and_recent_runs_in_launch_order(tmp_path):
    _store(
        tmp_path,
        ("old", "2026-09-25T01:00:00+00:00", "2026-09-25T02:00:00+00:00"),
        ("open", "2026-09-25T04:00:00+00:00", None),
        ("recent", "2026-09-25T03:00:00+00:00", "2026-09-25T05:00:00+00:00"),
    )
    assert run_store.run_spans(tmp_path, "2026-09-25T03:00:00+00:00") == [
        ("recent", "2026-09-25T03:00:00+00:00", "2026-09-25T05:00:00+00:00"),
        ("open", "2026-09-25T04:00:00+00:00", None),
    ]


def test_run_spans_is_empty_with_no_store_or_no_runs_table(tmp_path):
    assert run_store.run_spans(tmp_path, "2026-09-25T00:00:00+00:00") == []
    sqlite3.connect(tmp_path / "cox.db").close()
    assert run_store.run_spans(tmp_path, "2026-09-25T00:00:00+00:00") == []


def test_cli_json_reports_one_row_per_hour_and_the_totals(tmp_path, capsys):
    start = datetime.datetime.now(UTC) - datetime.timedelta(minutes=10)
    _store(tmp_path, ("live", start.isoformat(), None))
    assert main(["stats", "lanes", "--runs-dir", str(tmp_path), "--hours", "3", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["rows"]) == 3
    assert out["totals"]["peak"] == 1


def test_cli_prints_the_table_and_the_totals_line(tmp_path, capsys):
    assert main(["stats", "lanes", "--runs-dir", str(tmp_path), "--hours", "2"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["hour", "avg", "busy", "peak", "idle", "min"]
    assert lines[-1].startswith("lanes: avg 0.0 busy, peak 0, idle ") and lines[-1].endswith(" min over 2 h")


def test_cli_refuses_fewer_than_one_hour(tmp_path):
    assert main(["stats", "lanes", "--runs-dir", str(tmp_path), "--hours", "0"]) == 2
