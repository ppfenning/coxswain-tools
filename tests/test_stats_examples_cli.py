import json

import pytest

from agent_tools.cli import main


def _task(runs, run, name, text):
    path = runs / run / "tasks" / "p1" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if isinstance(text, str) else json.dumps(text))


@pytest.fixture
def runs(tmp_path):
    root = tmp_path / "runs"
    good = {
        "ticket": "a", "date": "2026-09-24", "plan": {"steps": ["x"]}, "change_facts": {"module_count": 1},
        "handoff": {"complete": True}, "build": {"patch": "diff"}, "review": {"verdict": "approve"},
    }
    _task(root, "r1", "a", good)
    _task(root, "r1", "b", {**good, "ticket": "b", "date": "2026-09-01", "handoff": {"complete": False}})
    _task(root, "r1", "c", "{not json")
    _task(root, "r1", "d", {"ticket": "d"})
    return root


def test_handoff_lines_go_to_stdout_and_the_counts_to_stderr(runs, capsys):
    assert main(["stats", "examples", str(runs), "--role", "handoff"]) == 0
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert [r["label"] for r in rows] == ["yes", "no"]
    assert rows[0]["state"] == {"plan": "{'steps': ['x']}", "change_facts": "{'module_count': 1}"}
    assert captured.err.strip() == "read 4, written 2, skipped 2"


def test_review_charter_uses_the_patch_and_the_ticket(runs, capsys):
    assert main(["stats", "examples", str(runs), "--role", "review_charter"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0] == {"state": {"patch": "diff", "plan": "a"}, "label": "approve"}


def test_out_writes_a_file_and_leaves_stdout_empty(runs, tmp_path, capsys):
    out = tmp_path / "examples.jsonl"
    assert main(["stats", "examples", str(runs), "--role", "handoff", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(out.read_text().splitlines()) == 2
    assert "written 2" in captured.err


def test_since_drops_an_older_record(runs, capsys):
    assert main(["stats", "examples", str(runs), "--role", "handoff", "--since", "2026-09-24"]) == 0
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1
    assert captured.err.strip() == "read 4, written 1, skipped 3"


def test_an_unknown_role_exits_two_naming_both_roles(runs, capsys):
    assert main(["stats", "examples", str(runs), "--role", "arbitrate"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "handoff" in captured.err and "review_charter" in captured.err
