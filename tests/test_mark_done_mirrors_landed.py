import datetime
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_tools import cli, store_cli

_RECORD = {"task": "seams-task", "state": "approved"}
_PR = "https://example.test/pr/7"
_AT = "2026-09-25T10:00:00+00:00"


def _step(tmp_path: Path, **extra) -> dict:
    path = tmp_path / "runs" / "run-1" / "tasks" / "seams" / "seams-task.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_RECORD), encoding="utf-8")
    return {"kind": "mark_done", "task": "seams-task", "path": str(path), **extra}


def _mirrored(tmp_path: Path) -> dict:
    return _step(tmp_path, run="run-1", phase="seams", pr=_PR, at=_AT)


@pytest.fixture
def harness(monkeypatch):
    """Patch subprocess.run; `calls` records argv, `outcome` is a (code, stdout, stderr) or an exception."""
    state = SimpleNamespace(calls=[], outcome=(0, json.dumps({"landed": True}), ""))

    def fake_run(argv, **kwargs):
        state.calls.append(argv)
        if isinstance(state.outcome, Exception):
            raise state.outcome
        code, out, err = state.outcome
        return subprocess.CompletedProcess(argv, code, out, err)

    monkeypatch.setattr("agent_tools.store_cli._harness_python", lambda: Path("/fake/python"))
    monkeypatch.setattr(subprocess, "run", fake_run)
    return state


def _rewritten(path: str) -> str:
    return json.dumps({**_RECORD, "landed": True}, indent=2)


def test_exit_0_mirrors_with_run_phase_task_pr_and_time_and_prints_nothing_extra(tmp_path, harness, capsys):
    step = _mirrored(tmp_path)
    ok, detail = cli._execute_land_step(tmp_path, step)
    assert ok is True
    runs_dir = Path(step["path"]).resolve().parents[3]
    assert harness.calls == [["/fake/python", "-m", "harness.store_cli", "mark-landed", "run-1", "seams", "seams-task",
                              "--pr", _PR, "--at", _AT, "--store-url", store_cli._store_url(runs_dir)]]
    assert capsys.readouterr().out == ""
    assert Path(step["path"]).read_text(encoding="utf-8") == _rewritten(step["path"])


def test_exit_3_says_the_record_predates_the_mirror_and_carries_on(tmp_path, harness, capsys):
    harness.outcome = (3, "", "")
    step = _mirrored(tmp_path)
    ok, detail = cli._execute_land_step(tmp_path, step)
    assert ok is True
    assert "store mirror of seams-task skipped: the record predates the mirror" in capsys.readouterr().out
    assert Path(step["path"]).read_text(encoding="utf-8") == _rewritten(step["path"])


def test_another_exit_code_warns_and_the_step_still_succeeds(tmp_path, harness, capsys):
    harness.outcome = (1, "", "boom")
    step = _mirrored(tmp_path)
    ok, detail = cli._execute_land_step(tmp_path, step)
    assert ok is True
    assert "warning: store mirror of seams-task failed (exit 1: boom)" in capsys.readouterr().out
    assert Path(step["path"]).read_text(encoding="utf-8") == _rewritten(step["path"])


def test_a_failed_spawn_warns_and_the_step_still_succeeds(tmp_path, harness, capsys):
    harness.outcome = OSError("no such file")
    step = _mirrored(tmp_path)
    ok, detail = cli._execute_land_step(tmp_path, step)
    assert ok is True
    assert "warning: store mirror of seams-task failed (exit -1: no such file)" in capsys.readouterr().out


def test_an_unexpected_raise_in_the_wrapper_warns_and_the_step_still_succeeds(tmp_path, monkeypatch, capsys):
    def explode(*args):
        raise RuntimeError("wrapper bug")

    monkeypatch.setattr("agent_tools.store_cli.mark_landed", explode)
    ok, detail = cli._execute_land_step(tmp_path, _mirrored(tmp_path))
    assert ok is True
    assert "warning: store mirror of seams-task failed (RuntimeError: wrapper bug)" in capsys.readouterr().out


def test_a_missing_harness_warns_and_the_step_still_succeeds(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("agent_tools.store_cli._harness_python", lambda: None)
    ok, detail = cli._execute_land_step(tmp_path, _mirrored(tmp_path))
    assert ok is True
    assert "harness not available" in capsys.readouterr().out


def test_a_step_without_the_store_facts_is_not_mirrored(tmp_path, harness, capsys):
    ok, detail = cli._execute_land_step(tmp_path, _step(tmp_path))
    assert ok is True
    assert harness.calls == []
    assert capsys.readouterr().out == ""


def test_mark_done_facts_reads_run_and_phase_from_the_record_path(tmp_path):
    step = cli._mark_done_facts(_step(tmp_path), _PR, _AT)
    assert (step["run"], step["phase"], step["pr"], step["at"]) == ("run-1", "seams", _PR, _AT)


@pytest.mark.parametrize("outcome", [(0, "{}", ""), (3, "", ""), (1, "", "boom"), OSError("gone")])
def test_the_land_walk_returns_success_and_supplies_pr_and_an_iso_time(tmp_path, harness, outcome):
    harness.outcome = outcome
    step = _step(tmp_path)
    rc, reached, pr = cli._land_walk(tmp_path, [step], [step], None, None, "merge", False, None, [])
    assert (rc, reached) == (0, ["mark_done"])
    argv = harness.calls[0]
    assert argv[argv.index("--pr") + 1] == ""
    datetime.datetime.fromisoformat(argv[argv.index("--at") + 1])
    assert Path(step["path"]).read_text(encoding="utf-8") == _rewritten(step["path"])
