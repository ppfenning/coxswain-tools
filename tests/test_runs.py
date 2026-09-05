import json

import pytest

from agent_tools.cli import build_parser
from agent_tools.runs import events


def _write_exited_run(runs_dir):
    (runs_dir / "run1.log").write_text(
        "run1 started\n"
        "epic run1: 1 phase(s) complete, 1 task(s) landed, 0 task(s) quarantined\n",
        encoding="utf-8",
    )
    (runs_dir / "run1.usage.json").write_text(
        json.dumps({"summary": {"cost_usd": 1.5, "turns": 10}}), encoding="utf-8"
    )


def _write_live_run(runs_dir):
    (runs_dir / "run2.log").write_text("run2 started\n", encoding="utf-8")
    trace_dir = runs_dir / "run2-trace"
    trace_dir.mkdir()
    (trace_dir / "build-1.jsonl").write_text("", encoding="utf-8")


def test_events_yields_expected_kinds_in_order(tmp_path):
    _write_exited_run(tmp_path)
    _write_live_run(tmp_path)
    kinds = [(e.run, e.kind) for e in events(tmp_path)]
    assert kinds == [
        ("run1", "run_started"),
        ("run1", "run_exited"),
        ("run1", "run_exited_cost"),
        ("run2", "run_started"),
        ("run2", "node_started"),
    ]


def test_follow_reads_only_the_logs_new_lines_not_the_whole_log_again(tmp_path):
    (tmp_path / "run2.log").write_text(
        "run2 started\n"
        "  review -> approve\n",
        encoding="utf-8",
    )
    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            with (tmp_path / "run2.log").open("a", encoding="utf-8") as fh:
                fh.write("  build -> approve\n")
            return
        raise StopIteration

    gen = events(tmp_path, follow=True, sleep=fake_sleep)
    collected = []
    with pytest.raises(RuntimeError):
        while True:
            collected.append(next(gen))

    # A re-send of the whole log on the follow round would repeat the "review"
    # verdict already delivered on the first pass; only "build" is new.
    verdicts = [e.detail["node"] for e in collected if e.kind == "verdict"]
    assert verdicts == ["review", "build"]
    assert calls["n"] == 2


def test_a_malformed_usage_file_is_retried_until_valid(tmp_path):
    (tmp_path / "run4.log").write_text("run4 started\n", encoding="utf-8")
    (tmp_path / "run4.usage.json").write_text("{not valid json", encoding="utf-8")
    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            (tmp_path / "run4.usage.json").write_text(
                json.dumps({"summary": {"cost_usd": 0.5, "turns": 2}}), encoding="utf-8"
            )
            return
        raise StopIteration

    gen = events(tmp_path, follow=True, sleep=fake_sleep)
    collected = []
    with pytest.raises(RuntimeError):
        while True:
            collected.append(next(gen))

    assert [e.kind for e in collected] == ["run_started", "run_exited_cost"]
    assert calls["n"] == 2


def test_follow_stops_following_a_run_once_it_exited_even_without_a_usage_file(tmp_path):
    (tmp_path / "run5.log").write_text("run5 started\n", encoding="utf-8")
    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            with (tmp_path / "run5.log").open("a", encoding="utf-8") as fh:
                fh.write("epic run5: 1 phase(s) complete, 1 task(s) landed, 0 task(s) quarantined\n")
            return
        raise AssertionError("follow kept polling an exited run that will never get a usage file")

    # Nothing in this repository writes <run>.usage.json, so exit-without-usage is
    # the normal case: follow must end once run_exited fires, not wait forever.
    kinds = [e.kind for e in events(tmp_path, follow=True, sleep=fake_sleep)]
    assert kinds == ["run_started", "run_exited"]
    assert calls["n"] == 1


def test_a_usage_file_present_in_the_same_pass_as_the_exit_line_is_still_emitted(tmp_path):
    _write_exited_run(tmp_path)
    kinds = [e.kind for e in events(tmp_path, follow=True, sleep=lambda s: None)]
    assert kinds == ["run_started", "run_exited", "run_exited_cost"]


def test_follow_recovers_after_the_log_is_truncated(tmp_path):
    (tmp_path / "run6.log").write_text("run6 started\n", encoding="utf-8")
    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            with (tmp_path / "run6.log").open("a", encoding="utf-8") as fh:
                fh.write("  review -> approve\n")
            return
        if calls["n"] == 2:
            (tmp_path / "run6.log").write_text("  build -> approve\n", encoding="utf-8")
            return
        if calls["n"] == 3:
            return  # a quiet pass after the truncation must add nothing
        raise StopIteration

    gen = events(tmp_path, follow=True, sleep=fake_sleep)
    collected = []
    with pytest.raises(RuntimeError):
        while True:
            collected.append(next(gen))

    verdicts = [e.detail["node"] for e in collected if e.kind == "verdict"]
    assert verdicts == ["review", "build"]  # the truncated log's lines are emitted once, not on every later pass
    assert calls["n"] == 4


def test_cli_runs_events_json_parses(tmp_path, capsys):
    _write_exited_run(tmp_path)
    args = build_parser().parse_args(["runs", "events", "--runs-dir", str(tmp_path), "--json"])
    assert args.fn(args) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["run"] == "run1" and parsed[0]["kind"] == "run_started"
    assert parsed[1]["kind"] == "run_exited"
    assert parsed[2]["kind"] == "run_exited_cost"


def test_cli_runs_events_plain_text_output(tmp_path, capsys):
    (tmp_path / "run3.log").write_text("run3 started\n", encoding="utf-8")
    args = build_parser().parse_args(["runs", "events", "--runs-dir", str(tmp_path)])
    assert args.fn(args) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["run3 run_started"]
