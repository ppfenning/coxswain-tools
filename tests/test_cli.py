import argparse
import json
import os
import subprocess as sp
import sys

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


def _launcher_ns(profile_path, **overrides):
    base = {"launcher_profile": str(profile_path), "no_plugin": True, "print_argv": False}
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def launcher_profile(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(f"workspace_dir: {workspace}\n", encoding="utf-8")
    return profile_path, workspace


def test__spawn_starts_a_detached_process(monkeypatch):
    calls = []

    class _Proc:
        pid = 4242

    def _fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return _Proc()

    monkeypatch.setattr(cli.subprocess, "Popen", _fake_popen)
    proc = cli._spawn(["x", "y"])
    assert proc.pid == 4242
    [(argv, kwargs)] = calls
    assert argv == ["x", "y"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == sp.DEVNULL


def test_bare_launcher_spawns_exactly_one_detached_beater_naming_its_pid(launcher_profile, monkeypatch, capsys):
    profile_path, workspace = launcher_profile
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(cli, "_route_chair_take", lambda a: 0)
    monkeypatch.setattr(cli.os, "chdir", lambda *a: None)
    monkeypatch.setattr(cli.os, "execvp", lambda *a: None)
    spawns = []

    class _Proc:
        pid = 9999

    def _fake_spawn(argv):
        spawns.append(argv)
        return _Proc()

    monkeypatch.setattr(cli, "_spawn", _fake_spawn)
    rc = cli._launcher(_launcher_ns(profile_path), [])
    out = capsys.readouterr().out
    assert rc == 0
    [argv] = spawns
    assert argv[:4] == [sys.executable, "-m", "agent_tools.chair", "beat-loop"]
    assert "--label" in argv and argv[argv.index("--label") + 1].startswith("chair-")
    assert "--pid" in argv and argv[argv.index("--pid") + 1] == str(os.getpid())
    assert "--runs-dir" in argv and argv[argv.index("--runs-dir") + 1] == str(workspace / "runs")
    assert "chair: beating from pid 9999" in out


def test_a_spawn_failure_is_printed_and_claude_still_execs(launcher_profile, monkeypatch, capsys):
    profile_path, _workspace = launcher_profile
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(cli, "_route_chair_take", lambda a: 0)
    monkeypatch.setattr(cli.os, "chdir", lambda *a: None)

    def _raising_spawn(argv):
        raise OSError("no such file or directory")

    monkeypatch.setattr(cli, "_spawn", _raising_spawn)
    execs = []
    monkeypatch.setattr(cli.os, "execvp", lambda *a: execs.append(a))
    rc = cli._launcher(_launcher_ns(profile_path), [])
    out = capsys.readouterr().out
    assert rc == 0
    assert "chair: beater failed to start: no such file or directory" in out
    assert execs and execs[0][0] == "claude"


def test_print_argv_takes_no_lock_and_spawns_nothing(launcher_profile, monkeypatch, capsys):
    profile_path, _workspace = launcher_profile
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")
    take_calls = []
    spawn_calls = []
    monkeypatch.setattr(cli, "_route_chair_take", lambda a: take_calls.append(a) or 0)
    monkeypatch.setattr(cli, "_spawn", lambda argv: spawn_calls.append(argv) or None)
    rc = cli._launcher(_launcher_ns(profile_path, print_argv=True), [])
    capsys.readouterr()
    assert rc == 0
    assert take_calls == []
    assert spawn_calls == []


def test_release_dry_run_reads_component_versions_from_checkouts_and_bumps_each_below_target(tmp_path, capsys, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(
        '[coxswain]\nversion = "0.1.0"\n\n'
        '[components.harness]\nrepo = "org/harness"\ntag = "v0.1.0"\n\n'
        '[components.cartridges]\nrepo = "org/cartridges"\ntag = "v0.1.0"\n'
    )
    for name in ("harness", "cartridges"):
        component_dir = tmp_path / name
        component_dir.mkdir()
        (component_dir / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    monkeypatch.setattr(cli, "_maintainer_remote_url", lambda directory: "git@github.com:ppfenning/coxswain.git")
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    rc = cli.main(["dev", "release", "0.2.0", "--dry-run", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    for name in ("harness", "cartridges"):
        assert f"bump_pyproject {name}:" in out
        assert "'from': '0.1.0'" in out and "'to': '0.2.0'" in out


def test_release_check_checkout_override_resolves_the_named_directory_not_the_coxswain_prefix_fallback(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(
        '[coxswain]\nversion = "0.2.0"\n\n'
        '[components.graphs]\nrepo = "org/graphs"\ntag = "v0.1.0"\n'
    )
    override_dir = tmp_path / "custom-graphs-checkout"
    override_dir.mkdir()
    (override_dir / "pyproject.toml").write_text('[project]\nversion = "0.1.5"\n')
    rc = cli.main(["dev", "release-check", "--manifest", str(manifest_path), "--root", str(tmp_path),
                   "--checkout", f"graphs={override_dir}", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    versions_drift = next(d for d in payload["drifts"] if d["check"] == "versions" and "0.1.5" in d["correction"])
    assert "0.2.0" in versions_drift["correction"]
    assert versions_drift["b_file"] == str(override_dir / "pyproject.toml")
