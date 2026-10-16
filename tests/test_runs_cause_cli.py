import argparse
import json

import pytest

from agent_tools import cli

_R1 = "  - {run: r1, phase: p1, reason: budget, ts: '2026-09-05T12:20:45+00:00'}\n"
_R2 = "  - {run: r2, phase: p1, reason: 'fix loop: 3 rounds'}\n"
_ITEM = f"---\nid: t1\nstate: ready\nattempts:\n{_R1}{_R2}lint: []\n---\nbody\n"
_OTHER = "---\nid: t2\nstate: ready\nattempts:\n  - {run: r2, phase: p1}\n---\nbody\n"


def _workspace(tmp_path):
    ws = tmp_path / "ws"
    for run in ("r1", "r2", "r9"):
        tasks = ws / "runs" / run / "tasks" / "p1"
        tasks.mkdir(parents=True)
        (tasks / "t1.json").write_text(json.dumps({"ticket": "t1", "landed": False}), encoding="utf-8")
    work = ws / "work" / "acme" / "p1"
    work.mkdir(parents=True)
    (work / "t1.md").write_text(_ITEM, encoding="utf-8")
    (work / "t2.md").write_text(_OTHER, encoding="utf-8")
    return ws, work


def _args(ws, run="r2", task="t1", cause="code", note=None):
    return argparse.Namespace(run_id=run, task_id=task, cause=cause, note=note, runs_dir=str(ws / "runs"), profile=None)


def test_cause_edits_the_attempt_of_the_named_run_and_nothing_else(tmp_path, capsys):
    ws, work = _workspace(tmp_path)
    assert cli._runs_cause(_args(ws, note="fixture drifted")) == 0
    assert (work / "t1.md").read_text(encoding="utf-8") == _ITEM.replace(
        _R2, "  - {run: r2, phase: p1, reason: 'fix loop: 3 rounds', cause: code, note: fixture drifted}\n"
    )
    assert (work / "t2.md").read_text(encoding="utf-8") == _OTHER
    assert capsys.readouterr().out.strip() == "t1: attempt r2 cause = code"


def test_cause_for_an_earlier_run_edits_that_runs_attempt_not_the_newest(tmp_path):
    ws, work = _workspace(tmp_path)
    assert cli._runs_cause(_args(ws, run="r1", cause="harness")) == 0
    assert (work / "t1.md").read_text(encoding="utf-8") == _ITEM.replace(
        _R1, "  - {run: r1, phase: p1, reason: budget, ts: '2026-09-05T12:20:45+00:00', cause: harness}\n"
    )


def test_cause_exits_two_and_writes_nothing_when_no_attempt_carries_the_run(tmp_path, capsys):
    ws, work = _workspace(tmp_path)
    assert cli._runs_cause(_args(ws, run="r9")) == 2
    assert (work / "t1.md").read_text(encoding="utf-8") == _ITEM
    assert "no attempts entry for run r9" in capsys.readouterr().out


def test_cause_without_a_note_writes_no_note_key(tmp_path):
    ws, work = _workspace(tmp_path)
    assert cli._runs_cause(_args(ws, cause="unknown")) == 0
    assert "note" not in (work / "t1.md").read_text(encoding="utf-8")


def test_cause_exits_two_when_the_run_has_no_task_record(tmp_path):
    ws, work = _workspace(tmp_path)
    assert cli._runs_cause(_args(ws, task="t9")) == 2
    assert (work / "t1.md").read_text(encoding="utf-8") == _ITEM


def test_a_cause_outside_the_four_is_refused_by_the_parser(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.main(["runs", "cause", "r2", "t1", "bogus", "--runs-dir", str(tmp_path)])
    assert exc.value.code != 0


def _stamped(path):
    return "cause: code" in path.read_text(encoding="utf-8")


def _record(ws, initiative):
    for run in ("r1", "r2"):
        path = ws / "runs" / run / "tasks" / "p1" / "t1.json"
        path.write_text(json.dumps({"ticket": "t1", "initiative": initiative}), encoding="utf-8")


def test_cause_finds_the_task_in_its_recorded_phase(tmp_path):
    ws, work = _workspace(tmp_path)
    _record(ws, "acme")
    assert cli._runs_cause(_args(ws)) == 0
    assert _stamped(work / "t1.md")


def test_cause_finds_a_task_moved_to_another_phase_and_stamps_the_given_run(tmp_path):
    ws, work = _workspace(tmp_path)
    _record(ws, "acme")
    moved = ws / "work" / "acme" / "p2"
    moved.mkdir()
    (work / "t1.md").rename(moved / "t1.md")
    assert cli._runs_cause(_args(ws)) == 0
    assert (moved / "t1.md").read_text(encoding="utf-8") == _ITEM.replace(
        _R2, "  - {run: r2, phase: p1, reason: 'fix loop: 3 rounds', cause: code}\n"
    )


def test_cause_resolves_the_same_task_id_inside_the_records_initiative(tmp_path):
    ws, work = _workspace(tmp_path)
    _record(ws, "acme")
    (work / "t1.md").unlink()
    mine, theirs = ws / "work" / "acme" / "p2", ws / "work" / "other" / "p1"
    mine.mkdir()
    theirs.mkdir(parents=True)
    (mine / "t1.md").write_text(_ITEM, encoding="utf-8")
    (theirs / "t1.md").write_text(_ITEM, encoding="utf-8")
    assert cli._runs_cause(_args(ws)) == 0
    assert _stamped(mine / "t1.md")
    assert (theirs / "t1.md").read_text(encoding="utf-8") == _ITEM


def test_cause_refuses_naming_zero_when_no_item_has_the_id(tmp_path, capsys):
    ws, work = _workspace(tmp_path)
    _record(ws, "acme")
    (work / "t1.md").unlink()
    other = ws / "work" / "other" / "p1"
    other.mkdir(parents=True)
    (other / "t1.md").write_text(_ITEM, encoding="utf-8")
    assert cli._runs_cause(_args(ws)) == 2
    assert "found 0" in capsys.readouterr().out
    assert (other / "t1.md").read_text(encoding="utf-8") == _ITEM


def test_pick_work_item_matches_one_within_the_initiative_and_counts_the_rest():
    paths = ["acme/p2/t1.md", "other/p1/t1.md", "acme/p1/t2.md", "acme/p3/t3.md", "acme/p4/t3.md"]
    assert cli._pick_work_item(paths, "t1", "acme") == ("acme/p2/t1.md", 1)
    assert cli._pick_work_item(paths, "t3", "acme") == (None, 2)
    assert cli._pick_work_item(paths, "t9", "acme") == (None, 0)
