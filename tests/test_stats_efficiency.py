import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from agent_tools.cli import main
from agent_tools.stats_efficiency import efficiency, first_land_days, landed_lands, render_lines, to_json

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
SINCE = "2026-09-20"


def _call(day, task, cost, turns, cache, total):
    return {"day": day, "task_id": task, "cost_usd": cost, "turns": turns, "cache_read_tokens": cache, "input_total": total}


def _task(task, builds, last_ts):
    return {"task_id": task, "builds": builds, "last_ts": last_ts}


CALLS = [
    _call("2026-09-22", "t1", 1.0, 4, 10, 40),
    _call("2026-09-22", "t2", 3.0, 6, 30, 60),
    _call("2026-09-22", "t5", 1.0, 0, 0, 0),
    _call("2026-09-22", None, 1.0, 0, 0, 0),
    _call("2026-09-23", "t3", 2.0, 5, 0, 0),
    _call("2026-09-25", "t4", 9.0, 1, 0, 0),
]
TASKS = [
    _task("t1", 1, "2026-09-22T10:00:00+00:00"), _task("t2", 3, "2026-09-22T11:00:00+00:00"),
    _task("t3", 2, "2026-09-23T09:00:00+00:00"), _task("t4", 1, "2026-09-25T06:00:00+00:00"),
    _task("t5", 2, "2026-09-22T12:00:00+00:00"),
]
LANDS = [("2026-09-22", "t1"), ("2026-09-22", "t5"), ("2026-09-01", "old")]


def _by_day():
    rows, total = efficiency(CALLS, TASKS, LANDS, SINCE, NOW)
    return {r.day: r for r in rows}, total


def test_the_days_are_the_days_with_calls_or_lands_since_the_cutoff_oldest_first():
    rows, _ = _by_day()
    assert list(rows) == ["2026-09-22", "2026-09-23", "2026-09-25"]


def test_cost_and_turns_are_the_day_sums_and_cost_per_turn_is_their_ratio():
    day = _by_day()[0]["2026-09-22"]
    assert (day.cost_usd, day.turns, day.cost_per_turn) == (6.0, 10, pytest.approx(0.6))


def test_cost_per_landed_is_the_days_cost_over_the_days_lands():
    day = _by_day()[0]["2026-09-22"]
    assert (day.landed, day.cost_per_landed) == (2, 3.0)


def test_first_try_rate_is_the_landed_tasks_with_exactly_one_build_call():
    assert _by_day()[0]["2026-09-22"].first_try_rate == 0.5


def test_waste_share_is_the_task_spend_on_tasks_that_never_landed():
    assert _by_day()[0]["2026-09-22"].waste_share == pytest.approx(3.0 / 5.0)


def test_a_task_whose_last_call_is_under_two_days_old_is_left_out_of_waste():
    day = _by_day()[0]["2026-09-25"]
    assert (day.cost_usd, day.waste_share) == (9.0, None)


def _waste_of_one_unlanded_task(last_ts):
    return efficiency([_call("2026-09-22", "t9", 1.0, 1, 0, 0)], [_task("t9", 1, last_ts)], [], SINCE, NOW)[1].waste_share


def test_a_last_call_exactly_two_days_old_counts_toward_waste():
    assert _waste_of_one_unlanded_task("2026-09-23T12:00:00+00:00") == 1.0


def test_a_last_call_one_second_under_two_days_old_is_left_out():
    assert _waste_of_one_unlanded_task("2026-09-23T12:00:01+00:00") is None


def test_a_naive_last_call_reads_as_utc():
    assert (_waste_of_one_unlanded_task("2026-09-23T12:00:01"), _waste_of_one_unlanded_task("2026-09-23T11:59:59")) == (None, 1.0)


def test_an_unparseable_or_missing_last_call_reads_as_old():
    assert (_waste_of_one_unlanded_task("yesterday"), _waste_of_one_unlanded_task(None)) == (1.0, 1.0)


def test_a_task_that_failed_in_one_run_and_landed_in_a_retry_is_not_waste_and_not_first_try():
    calls = [_call("2026-09-22", "t7", 2.0, 3, 0, 0), _call("2026-09-23", "t7", 1.0, 2, 0, 0)]
    rows, total = efficiency(calls, [_task("t7", 2, "2026-09-23T10:00:00+00:00")], [("2026-09-23", "t7")], SINCE, NOW)
    assert [(r.day, r.waste_share, r.landed, r.first_try_rate) for r in rows] == [
        ("2026-09-22", 0.0, 0, None), ("2026-09-23", 0.0, 1, 0.0),
    ]
    assert (total.waste_share, total.first_try_rate) == (0.0, 0.0)


def test_cache_read_share_is_cache_read_tokens_over_input_total():
    days, _ = _by_day()
    assert (days["2026-09-22"].cache_read_share, days["2026-09-23"].cache_read_share) == (pytest.approx(0.4), None)


def test_a_day_with_no_lands_has_no_cost_per_landed_or_first_try_rate_but_still_shows_its_waste():
    day = _by_day()[0]["2026-09-23"]
    assert (day.landed, day.cost_per_landed, day.first_try_rate, day.waste_share) == (0, None, None, 1.0)


def test_totals_sum_the_window_and_ignore_a_land_before_the_cutoff():
    _, total = _by_day()
    assert (total.day, total.cost_usd, total.turns, total.landed, total.cost_per_landed, total.first_try_rate) == ("total", 17.0, 16, 2, 8.5, 0.5)
    assert total.waste_share == pytest.approx(5.0 / 7.0)


def test_a_task_landed_twice_counts_once_on_its_first_day_so_the_days_sum_to_the_total():
    calls = [_call("2026-09-22", "t1", 1.0, 1, 0, 0), _call("2026-09-24", "t1", 1.0, 1, 0, 0)]
    lands = [("2026-09-24", "t1"), ("2026-09-22", "t1"), ("2026-09-22", "t1")]
    rows, total = efficiency(calls, [_task("t1", 1, "2026-09-24T00:00:00+00:00")], lands, SINCE, NOW)
    assert ([r.landed for r in rows], total.landed) == ([1, 0], 1)


def test_first_land_days_keeps_the_earliest_dated_land_per_task():
    assert first_land_days([("2026-09-24", "a"), ("", "a"), ("2026-09-22", "a"), ("2026-09-23", "b")]) == {"a": "2026-09-22", "b": "2026-09-23"}


def test_a_task_landed_before_the_window_or_with_no_ts_is_not_waste_and_not_counted():
    calls = [_call("2026-09-22", "old", 4.0, 1, 0, 0), _call("2026-09-22", "t8", 2.0, 1, 0, 0)]
    tasks = [_task("old", 1, "2026-09-22T00:00:00+00:00"), _task("t8", 1, "2026-09-22T00:00:00+00:00")]
    rows, total = efficiency(calls, tasks, [("2026-09-01", "old"), ("", "t8")], SINCE, NOW)
    assert (rows[0].waste_share, total.landed) == (0.0, 0)


def test_no_calls_and_no_lands_gives_no_days_and_an_all_none_total():
    rows, total = efficiency([], [], [], SINCE, NOW)
    assert (rows, total.cost_usd, total.cost_per_turn, total.waste_share) == ([], 0, None, None)


def test_landed_lands_keeps_exit_zero_with_mark_done_and_a_task_and_skips_the_rest():
    lines = [
        '{"ts": "2026-09-22T10:00:00+00:00", "run": "r", "task": "t1", "steps_reached": ["merge", "mark_done"], "exit": 0}',
        "not json",
        '{"ts": "2026-09-22T10:00:00+00:00", "run": "r", "task": "t2", "steps_reached": ["merge"], "exit": 0}',
        '{"ts": "2026-09-22T10:00:00+00:00", "run": "r", "task": "t3", "steps_reached": ["mark_done"], "exit": 1}',
        '{"ts": "2026-09-22T10:00:00+00:00", "run": "r", "task": null, "steps_reached": ["mark_done"], "exit": 0}',
        "[1, 2]",
        '{"run": "r", "task": "t6", "steps_reached": ["mark_done"], "exit": 0}',
    ]
    assert landed_lands(lines) == [("2026-09-22", "t1"), ("", "t6")]


def test_render_lines_dashes_a_missing_ratio_and_ends_with_the_total():
    days, total = _by_day()
    lines = render_lines(list(days.values()), total)
    assert lines[2].split() == ["2026-09-23", "2.00", "5", "0.400", "0", "-", "-", "100%", "-"]
    assert lines[-1].split()[:3] == ["total", "17.00", "16"]


def test_to_json_carries_each_day_and_the_total():
    rows, total = efficiency(CALLS, TASKS, LANDS, SINCE, NOW)
    out = to_json(rows, total)
    assert [d["day"] for d in out["days"]] == ["2026-09-22", "2026-09-23", "2026-09-25"]
    assert out["total"]["landed"] == 2


def _store(tmp_path, monkeypatch, calls):
    (tmp_path / "provider.yaml").write_text(yaml.safe_dump({}))
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump({"provider_profile": str(tmp_path / "provider.yaml")}))
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(tmp_path / "profile.yaml"))
    conn = sqlite3.connect(tmp_path / "cox.db")
    conn.execute(
        "CREATE TABLE node_calls (call_id TEXT, run_id TEXT, seq INTEGER, task_id TEXT, role TEXT, cost_usd REAL, turns INTEGER, "
        "cache_read_tokens INTEGER, input_total INTEGER, ts TEXT)"
    )
    conn.executemany("INSERT INTO node_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", calls)
    conn.commit()
    conn.close()


def test_the_command_keys_a_retried_task_by_its_task_id_across_runs(tmp_path, monkeypatch, capsys):
    day = (datetime.now(UTC) - timedelta(days=3)).date().isoformat()
    _store(tmp_path, monkeypatch, [
        ("c1", "x-1", 1, "t1", "build", 2.0, 4, 5, 10, f"{day}T10:00:00+00:00"),
        ("c2", "x-2", 1, "t1", "build", 1.0, 2, 0, 10, f"{day}T11:00:00+00:00"),
        ("c3", "x-2", 2, "t2", "build", 1.0, 2, 0, 20, f"{day}T11:30:00+00:00"),
    ])
    (tmp_path / "land.jsonl").write_text(
        json.dumps({"ts": f"{day}T12:00:00+00:00", "run": "x-2", "task": "t1", "steps_reached": ["mark_done"], "exit": 0}) + "\nnot json\n"
    )
    assert main(["stats", "efficiency", "--runs-dir", str(tmp_path), "--json"]) == 0
    total = json.loads(capsys.readouterr().out)["total"]
    assert (total["cost_usd"], total["landed"], total["cost_per_landed"], total["first_try_rate"], total["waste_share"]) == (4.0, 1, 4.0, 0.0, 0.25)


def test_the_command_on_a_runs_dir_with_no_store_prints_an_empty_total(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(tmp_path / "none.yaml"))
    assert main(["stats", "efficiency", "--runs-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines()[-1].split() == ["total", "0.00", "0", "-", "0", "-", "-", "-", "-"]


def test_days_below_one_is_refused(tmp_path, capsys):
    assert main(["stats", "efficiency", "--runs-dir", str(tmp_path), "--days", "0"]) == 2
    assert "--days must be at least 1" in capsys.readouterr().err
