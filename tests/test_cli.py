import argparse
import json
import subprocess as sp

import pytest

from agent_tools import cli


def _land_ns(**overrides):
    base = {"run_id": "epic-x-5", "repo": None, "task": None, "phase": None, "label": None, "force": False,
                "worktree_root": "~/worktrees", "apply": False, "no_merge": False, "runs_dir": None, "profile": None}
    base.update(overrides)
    return argparse.Namespace(**base)


def _work_item(work_root, initiative, phase, task_id, state):
    phase_dir = work_root / initiative / phase
    phase_dir.mkdir(parents=True, exist_ok=True)
    (phase_dir / f"{task_id}.md").write_text(
        f"---\nid: {task_id}\nphase: {phase}\nstate: {state}\nneeds: []\ntitle: Add seams\n---\n\nbody\n",
        encoding="utf-8",
    )


@pytest.fixture
def phase_runs_dir(tmp_path):
    runs_dir = tmp_path / "runs"
    tasks_dir = runs_dir / "epic-x-5" / "tasks" / "seams"
    tasks_dir.mkdir(parents=True)
    (runs_dir / "epic-x-5:seams.json").write_text(json.dumps({
        "phase": "seams", "initiative": "x", "phase_verdict": {"reasoning": "ok"},
    }), encoding="utf-8")
    (tasks_dir / "seams-task.json").write_text(json.dumps({
        "status": "done", "review": {"verdict": "approve"}, "arbitration": {"verdict": "approve"},
        "change_facts": {"fix_loop_attempts": 0, "files_touched": ["a.py"]}, "title": "Add seams",
        "proposals": [{"kind": "draft_pr_create", "title": "Add seams"}], "initiative": "x",
    }), encoding="utf-8")
    (tasks_dir / "seams-dropped.json").write_text(json.dumps({"status": "dropped"}), encoding="utf-8")
    _work_item(tmp_path / "work", "x", "seams", "seams-task", "done")
    _work_item(tmp_path / "work", "x", "seams", "seams-dropped", "dropped")
    return runs_dir


def test_phase_mode_dry_run_step_list_has_no_checkout_step(phase_runs_dir, tmp_path, capsys):
    repo = tmp_path / "repo"; repo.mkdir()
    ns = _land_ns(repo=str(repo), phase="seams", runs_dir=str(phase_runs_dir))
    rc = cli._runs_land(ns)
    steps = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [s["kind"] for s in steps] == ["pick_branch", "checks", "push", "pr_create", "wait_checks", "merge", "clean_phase", "mark_done"]
    checks = next(s for s in steps if s["kind"] == "checks")
    assert checks["branch"] == "epic/x/seams"
    clean = next(s for s in steps if s["kind"] == "clean_phase")
    assert clean["phase_branch"] == "epic/x/seams"
    assert clean["tasks"] == ["seams-task"]
    mark_done = next(s for s in steps if s["kind"] == "mark_done")
    assert mark_done["path"] == str(phase_runs_dir / "epic-x-5" / "tasks" / "seams" / "seams-task.json")


def test_phase_mode_dry_run_never_touches_the_filesystem_for_its_checks_step(phase_runs_dir, tmp_path, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(cli.tempfile, "mkdtemp", lambda *a, **k: calls.append(1) or str(tmp_path / "unused"))
    repo = tmp_path / "repo"; repo.mkdir()
    ns = _land_ns(repo=str(repo), phase="seams", runs_dir=str(phase_runs_dir))
    rc = cli._runs_land(ns)
    assert rc == 0
    assert calls == []


def test_default_mode_selects_phase_when_neither_flag_is_given(phase_runs_dir, tmp_path, capsys):
    repo = tmp_path / "repo"; repo.mkdir()
    ns = _land_ns(repo=str(repo), runs_dir=str(phase_runs_dir))
    rc = cli._runs_land(ns)
    steps = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [s["kind"] for s in steps] == ["pick_branch", "checks", "push", "pr_create", "wait_checks", "merge", "clean_phase", "mark_done"]


def test_task_flag_forces_task_mode_even_when_the_phase_has_two_records(phase_runs_dir, tmp_path, capsys):
    repo = tmp_path / "repo"; repo.mkdir()
    sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    ns = _land_ns(repo=str(repo), task="seams-task", runs_dir=str(phase_runs_dir))
    rc = cli._runs_land(ns)
    steps = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert steps == [{"kind": "refuse", "reason": "no branch is exactly one commit ahead of main", "found": {}}]
