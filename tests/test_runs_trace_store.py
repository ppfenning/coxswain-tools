import json
import sys

import pytest
from test_run_store import day_file

from agent_tools import cli as cli_module

CALLS = [{"role": "build", "id": "c1"}, {"role": "handoff", "id": "c2"}, {"role": "build", "id": "c3"}]


def result(turns, cost):
    return {"type": "result", "subtype": "success", "num_turns": turns, "total_cost_usd": cost}


def store_run(runs_dir, calls=CALLS):
    (runs_dir / "r.usage.json").write_text(json.dumps({"run_id": "r", "calls": calls}))
    rows = [{"call_id": "c1", "seq": 0, "event": result(3, 1.5)}, {"call_id": "c2", "seq": 0, "event": result(2, 0.5)},
            {"call_id": "c3", "seq": 0, "event": result(7, 2.0)}]
    day_file(runs_dir, "2026/09/25", "r", rows)


def nodes(out):
    return [line.split()[0] for line in out.splitlines()[1:]]


def test_loose_trace_files_are_read_as_before(tmp_path, capsys):
    d = tmp_path / "r-trace"
    d.mkdir()
    (d / "build-1.jsonl").write_text(json.dumps(result(3, 1.5)) + "\n")
    assert cli_module.main(["runs", "trace", "r", "--runs-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert nodes(out) == ["build-1"] and "1.50" in out


def test_a_loose_dir_with_files_wins_over_the_store(tmp_path, capsys):
    pytest.importorskip("zstandard")
    store_run(tmp_path)
    d = tmp_path / "r-trace"
    d.mkdir()
    (d / "build-1.jsonl").write_text(json.dumps(result(9, 4.0)) + "\n")
    assert cli_module.main(["runs", "trace", "r", "--runs-dir", str(tmp_path)]) == 0
    assert nodes(capsys.readouterr().out) == ["build-1"]


def test_an_empty_trace_dir_reads_the_usage_and_the_trace_store(tmp_path, capsys):
    pytest.importorskip("zstandard")
    store_run(tmp_path)
    (tmp_path / "r-trace").mkdir()
    assert cli_module.main(["runs", "trace", "r", "--runs-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert nodes(out) == ["build-1", "build-2", "handoff-1"]
    assert "2.00" in out and "0.50" in out


def test_with_no_trace_dir_the_table_is_read_from_usage_and_the_trace_store(tmp_path, capsys):
    pytest.importorskip("zstandard")
    store_run(tmp_path)
    assert cli_module.main(["runs", "trace", "r", "--runs-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert nodes(out) == ["build-1", "build-2", "handoff-1"]
    assert "2.00" in out and "0.50" in out


def test_role_keeps_only_that_roles_calls_with_their_numbers(tmp_path, capsys):
    pytest.importorskip("zstandard")
    store_run(tmp_path, [{"role": "handoff", "id": "c2"}, {"role": "build", "id": "c1"}])
    assert cli_module.main(["runs", "trace", "r", "--runs-dir", str(tmp_path), "--role", "build"]) == 0
    assert nodes(capsys.readouterr().out) == ["build-1"]


def test_no_usage_and_no_trace_dir_exits_2_with_the_message(tmp_path, capsys):
    assert cli_module.main(["runs", "trace", "ghost", "--runs-dir", str(tmp_path)]) == 2
    assert capsys.readouterr().out.strip() == f"no trace for ghost in {tmp_path}"


def test_missing_zstandard_exits_2_with_its_message(tmp_path, capsys, monkeypatch):
    (tmp_path / "r.usage.json").write_text(json.dumps({"run_id": "r", "calls": CALLS}))
    (tmp_path / "traces").mkdir()
    monkeypatch.setitem(sys.modules, "zstandard", None)
    assert cli_module.main(["runs", "trace", "r", "--runs-dir", str(tmp_path)]) == 2
    assert "coxswain-tools[traces]" in capsys.readouterr().out


def summarised(role, call_id, turns, cost, command):
    return {
        "role": role, "id": call_id, "turns": turns, "cost_usd": cost,
        "summary": {"result": "success", "is_error": False, "tool_uses": {"Bash": 1, "Read": 2}, "reads": {"a.py": 2}, "whole_file_reads": 1},
        "commands_run": [{"command": command}],
    }


def test_calls_carrying_summaries_print_the_table_without_reading_events(tmp_path, capsys, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("call_events must not be called")

    monkeypatch.setattr(cli_module.run_store, "call_events", boom)
    calls = [summarised("build", "c1", 3, 1.5, "pytest -q"), summarised("handoff", "c2", 2, 0.5, "ls")]
    (tmp_path / "r.usage.json").write_text(json.dumps({"run_id": "r", "calls": calls}))
    assert cli_module.main(["runs", "trace", "r", "--runs-dir", str(tmp_path), "-v"]) == 0
    out = capsys.readouterr().out
    assert "   $ pytest -q" in out
    assert [line.split()[:5] for line in out.splitlines()[-2:]] == [
        ["build-1", "3", "1.50", "success", "1"], ["handoff-1", "2", "0.50", "success", "1"],
    ]


def test_a_call_without_a_summary_still_reads_events(tmp_path, capsys, monkeypatch):
    seen = []

    def fake(runs_dir, run_id, call):
        seen.append(call["id"])
        return [result(7, 2.0)]

    monkeypatch.setattr(cli_module.run_store, "call_events", fake)
    calls = [summarised("build", "c1", 3, 1.5, "ls"), {"role": "build", "id": "c3"}]
    (tmp_path / "r.usage.json").write_text(json.dumps({"run_id": "r", "calls": calls}))
    assert cli_module.main(["runs", "trace", "r", "--runs-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert seen == ["c3"] and nodes(out) == ["build-1", "build-2"] and "2.00" in out and "1.50" in out
