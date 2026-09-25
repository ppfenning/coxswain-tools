import json

from agent_tools import cli, run_store, runs_detail
from agent_tools.runs_detail_screen import _tail, facts_for


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def test_facts_for_reads_the_task_record_and_the_newest_trace_tail(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    tasks = tmp_path / "r1" / "tasks" / "build"
    tasks.mkdir(parents=True)
    _write(tasks / "t1.json", json.dumps({"change_facts": {"files_touched": ["a.py"], "changed_lines": 3}}))
    trace = tmp_path / "r1-trace"
    trace.mkdir()
    _write(trace / "n1-1.jsonl", "\n".join(
        json.dumps(line) for line in [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit"}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
            {"type": "result", "total_cost_usd": 0.1, "num_turns": 3},
        ]
    ) + "\n")

    facts = facts_for(tmp_path, "r1", now_alive=lambda pid: True)

    assert facts["record"]["change_facts"]["files_touched"] == ["a.py"]
    assert facts["tail"] == ["Read", "Edit", "Bash"]
    assert facts["alive"] is True


def test_facts_for_is_empty_record_and_tail_with_no_task_or_trace(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")

    facts = facts_for(tmp_path, "r1", now_alive=lambda pid: False)

    assert facts["record"] == {}
    assert facts["tail"] == []
    assert facts["alive"] is False


def test_facts_for_returns_an_empty_record_for_a_torn_write_not_a_raise(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    tasks = tmp_path / "r1" / "tasks" / "build"
    tasks.mkdir(parents=True)
    _write(tasks / "t1.json", '{"change_facts": {"files_touc')  # truncated mid-write

    facts = facts_for(tmp_path, "r1", now_alive=lambda pid: True)

    assert facts["record"] == {}


def test_tail_ignores_a_stray_file_that_does_not_match_node_dash_attempt(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    trace = tmp_path / "r1-trace"
    trace.mkdir()
    _write(trace / "n1-1.jsonl", json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}) + "\n")
    _write(trace / "notes.jsonl", json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}) + "\n")

    facts = facts_for(tmp_path, "r1", now_alive=lambda pid: True)

    assert facts["tail"] == ["Read"]


def test_facts_for_feeds_a_json_arbitration_dict_into_the_objection(tmp_path):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: revise\n")
    tasks = tmp_path / "r1" / "tasks" / "build"
    tasks.mkdir(parents=True)
    _write(tasks / "t1.json", json.dumps({"arbitration": {"reasoning": "the frobnicator leaks fuel."}}))

    d = runs_detail.detail(**facts_for(tmp_path, "r1", now_alive=lambda pid: True))

    assert d.objection == "the frobnicator leaks fuel"


def test_cli_runs_detail_json_prints_the_trace_tail_from_the_underlying_record(tmp_path, capsys):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")
    trace = tmp_path / "r1-trace"
    trace.mkdir()
    _write(trace / "n1-1.jsonl", json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}) + "\n")

    rc = cli.main(["runs", "detail", "r1", "--runs-dir", str(tmp_path), "--json"])

    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "timeline" in out
    assert out["last_calls"] == ["Bash"]


def test_cli_runs_detail_default_prints_the_rendered_lines(tmp_path, capsys):
    _write(tmp_path / "r1.pid", "123")
    _write(tmp_path / "r1.log", "n1 verdict: land\n")

    rc = cli.main(["runs", "detail", "r1", "--runs-dir", str(tmp_path)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "run r1" in out


def test_a_trace_line_whose_message_is_a_string_is_skipped_not_raised():
    from agent_tools.runs_detail_screen import _tool_names

    events = [{"message": "resuming"}, {"message": {"content": [{"type": "tool_use", "name": "Read"}]}}, {"message": None}, "junk"]
    assert _tool_names(events) == ["Read"]


def _tool_use(name):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name}]}}


def test_tail_with_no_trace_files_reads_the_last_stored_calls_events(tmp_path, monkeypatch):
    first, last = {"id": "c1", "role": "plan"}, {"id": "c2", "role": "build"}
    monkeypatch.setattr(run_store, "usage", lambda root, run: {"calls": [first, last]})
    seen = []

    def events(root, run, call):
        seen.append(call)
        return [_tool_use(n) for n in ("Read", "Grep", "Edit", "Bash")]

    monkeypatch.setattr(run_store, "call_events", events)

    assert _tail(tmp_path, "r1") == ["Grep", "Edit", "Bash"]
    assert seen == [last]


def test_tail_with_no_trace_files_is_empty_when_the_trace_store_is_unavailable(tmp_path, monkeypatch):
    def unavailable(root, run, call):
        raise run_store.TracesUnavailable

    monkeypatch.setattr(run_store, "usage", lambda root, run: {"calls": [{"id": "c1", "role": "build"}]})
    monkeypatch.setattr(run_store, "call_events", unavailable)

    assert _tail(tmp_path, "r1") == []
