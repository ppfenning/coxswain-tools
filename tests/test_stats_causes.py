import json
import sqlite3

from agent_tools import cli, stats_causes
from agent_tools.stats_causes import CauseRow, summarise


def row(cause="timeout", kind="build", cause_why=None, reason=None):
    return {"cause": cause, "kind": kind, "cause_why": cause_why, "reason": reason}


def test_one_row_per_cause_with_count_and_share():
    got = summarise([row("a"), row("a"), row("a"), row("b")])
    assert [(s.cause, s.count, s.share) for s in got] == [("a", 3, 0.75), ("b", 1, 0.25)]


def test_causes_sort_by_count_then_name():
    assert [s.cause for s in summarise([row("z"), row("z"), row("b"), row("a")])] == ["z", "a", "b"]


def test_kinds_under_a_cause_count_by_kind_biggest_first():
    got = summarise([row(kind="review"), row(kind="build"), row(kind="build")])
    assert got[0].kinds == (("build", 2), ("review", 1))


def test_samples_are_capped_at_three():
    got = summarise([row(cause_why=f"w{i}") for i in range(5)])
    assert got[0].samples == ("w0", "w1", "w2")


def test_a_sample_falls_back_to_reason_when_cause_why_is_empty():
    got = summarise([row(cause_why="why", reason="r1"), row(cause_why="", reason="r2"), row(cause_why=None, reason="r3")])
    assert got[0].samples == ("why", "r2", "r3")


def test_a_repeated_or_blank_sample_is_dropped():
    got = summarise([row(cause_why="x"), row(cause_why="x"), row(cause_why=" ", reason=None)])
    assert got[0].samples == ("x",)


def test_an_attempt_with_no_cause_is_unrecorded_never_unknown():
    got = summarise([row(cause=None), row(cause=""), row(cause="unknown")])
    assert [(s.cause, s.count) for s in got] == [("unrecorded", 2), ("unknown", 1)]


def test_no_attempts_summarise_to_nothing():
    assert summarise([]) == []


def test_render_lines_shows_cause_share_kinds_and_samples():
    s = [CauseRow("timeout", 3, 0.75, (("build", 2), ("review", 1)), ("slow  run",))]
    assert stats_causes.render_lines(s) == ["3 quarantined attempts", "timeout  3  75%", "  kinds: build 2, review 1", "  - slow run"]


def test_render_lines_says_so_when_there_is_nothing():
    assert stats_causes.render_lines([]) == ["no quarantined attempts"]


def test_to_json_shape():
    s = [CauseRow("timeout", 1, 1 / 3, (("build", 1),), ("slow",))]
    assert stats_causes.to_json(s) == {
        "total": 1,
        "causes": [{"cause": "timeout", "count": 1, "share": 0.3333, "kinds": {"build": 1}, "samples": ["slow"]}],
    }


def _store(runs_dir):
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute("CREATE TABLE attempts (run_id TEXT, task_id TEXT, seq INTEGER, phase_id TEXT, kind TEXT, reason TEXT, ts TEXT)")
    conn.execute("INSERT INTO attempts (kind, reason, ts) VALUES ('build', 'boom', '2026-09-25T01:00:00+00:00')")
    conn.commit()
    conn.close()


def test_cli_rejects_a_non_canonical_since(tmp_path, capsys):
    assert cli.main(["stats", "causes", "--runs-dir", str(tmp_path), "--since", "20260901"]) == 2
    assert "--since must be a date as YYYY-MM-DD" in capsys.readouterr().err


def test_cli_json_reads_a_schema_4_store_as_unrecorded(tmp_path, capsys):
    _store(tmp_path)
    assert cli.main(["stats", "causes", "--runs-dir", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["causes"] == [
        {"cause": "unrecorded", "count": 1, "share": 1.0, "kinds": {"build": 1}, "samples": ["boom"]}
    ]


def test_cli_says_so_with_no_store(tmp_path, capsys):
    assert cli.main(["stats", "causes", "--runs-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "no quarantined attempts"
