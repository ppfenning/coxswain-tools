"""The land edge recreates an empty leftover pr branch whose cherry-picked tree it cannot compute, under the repo
lease and without touching the remote, and refuses one carrying commits not on main with `land.leftover_gate`'s text."""

import json
import os
import subprocess as sp

import pytest
from test_land import _PRESYNCED, _record

from agent_tools import cli, land_repo_lease, store_cli

_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
_TASK = "agents/epic-x-5/seams-task"
_PR = "pr/seams-task"
_STEP = {"kind": "cherry_pick", "branch": _TASK, "commit_subject": "Add seams module", "onto": _PR, "from": "main"}


def _git(repo, *args):
    return sp.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_ENV).stdout.strip()


def _has_ref(repo, ref):
    return sp.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", ref], capture_output=True).returncode == 0


@pytest.fixture(autouse=True)
def no_open_prs(monkeypatch):
    monkeypatch.setattr(cli, "_open_prs_for", lambda _repo, _branch, _forge=None: [])


@pytest.fixture
def repo(tmp_path):
    """main and a task branch carrying one commit plus a merge of main (one commit to land, yet no single
    cherry-picked tree), both on a real bare origin, and a local-only leftover `pr/seams-task` at main."""
    origin, root = tmp_path / "origin.git", tmp_path / "repo"
    sp.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    root.mkdir()
    sp.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    _git(root, "config", "user.name", "t")
    _git(root, "config", "user.email", "t@e")
    (root / "f").write_text("x")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "checkout", "-qb", _TASK)
    (root / "f").write_text("y")
    _git(root, "commit", "-aqm", "Add seams module")
    _git(root, "checkout", "-q", "main")
    (root / "g").write_text("main moves")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "main moves")
    _git(root, "checkout", "-q", _TASK)
    _git(root, "merge", "-q", "--no-ff", "--no-edit", "main")
    _git(root, "checkout", "-q", "main")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "origin", "main", _TASK)
    _git(root, "branch", _PR, "main")
    return root


def _remote_refs(repo):
    return _git(repo, "ls-remote", "origin")


def _land(repo, tmp_path, monkeypatch, lease):
    """`cox runs land --apply` with the steps faked and the repo lease answering `lease`; returns rc and steps run."""
    task_dir = tmp_path / "runs/epic-x-5/tasks/seams"
    task_dir.mkdir(parents=True)
    (task_dir / "seams-task.json").write_text(json.dumps(_record()), encoding="utf-8")
    (tmp_path / "runs/policy.tracker.json").write_text('{"tracker": "github-projects"}', encoding="utf-8")
    ran = []
    monkeypatch.setattr(cli, "_execute_land_step", lambda _repo, step, _forge=None: (ran.append(step["kind"]) or True, "https://x/pull/7"))
    monkeypatch.setattr(land_repo_lease, "acquire", lambda *_args: lease)
    monkeypatch.setattr(land_repo_lease, "release", lambda *_args: None)
    monkeypatch.setattr(land_repo_lease, "renew", lambda *_args: lease)
    rc = cli.main(["runs", "land", "epic-x-5", "--repo", str(repo), "--apply", "--runs-dir", str(tmp_path / "runs"), "--gate", "full"])
    return rc, ran


def test_the_tree_is_uncomputable_for_this_task_branch(repo):
    decision = cli._land_resume(repo, _STEP, None)
    assert decision["kind"] == "refuse" and decision["reason"].startswith("cannot compute the cherry-picked tree")


def test_an_empty_leftover_decides_a_fresh_land_from_its_old_tip(repo):
    old = _git(repo, "rev-parse", _PR)
    decision = cli._land_leftover(repo, _STEP, cli._land_resume(repo, _STEP, None), None)
    assert decision["kind"] == "fresh" and decision["old_tip"] == old and "backup_refs" not in decision
    assert _git(repo, "rev-parse", _PR) == old  # deciding writes nothing


def test_an_empty_leftover_is_recreated_and_the_land_continues_with_the_remote_untouched(repo, tmp_path, monkeypatch, capsys):
    old, before = _git(repo, "rev-parse", _PR), _remote_refs(repo)
    rc, ran = _land(repo, tmp_path, monkeypatch, store_cli.LeaseGranted(1, "me"))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"land: {_PR} at {old[:8]} holds nothing beyond main, recreating it\n" in out
    assert ran[:10] == _PRESYNCED
    assert not _has_ref(repo, f"refs/heads/{_PR}")
    assert _remote_refs(repo) == before
    assert _git(repo, "for-each-ref", "refs/backup") == ""


def test_a_leftover_with_a_commit_not_on_main_is_refused_naming_the_count(repo, tmp_path, monkeypatch, capsys):
    _git(repo, "checkout", "-q", _PR)
    (repo / "h").write_text("stray")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "stray work")
    _git(repo, "checkout", "-q", "main")
    tip = _git(repo, "rev-parse", _PR)
    rc, ran = _land(repo, tmp_path, monkeypatch, store_cli.LeaseGranted(1, "me"))
    out = capsys.readouterr().out
    assert (rc, ran) == (2, [])
    assert f"land: refusing, branch {_PR} already exists in {repo}: {_PR} has 1 commits not on main\n" in out
    assert _git(repo, "rev-parse", _PR) == tip


def test_a_rerun_after_the_branch_is_gone_proceeds_as_fresh(repo, tmp_path, monkeypatch, capsys):
    _git(repo, "branch", "-D", _PR)
    rc, ran = _land(repo, tmp_path, monkeypatch, store_cli.LeaseGranted(1, "me"))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "recreating" not in out and ran[:10] == _PRESYNCED


def test_recreate_finds_the_branch_already_gone_and_carries_on(repo):
    old = _git(repo, "rev-parse", _PR)
    _git(repo, "branch", "-D", _PR)
    assert cli._land_recreate(repo, _PR, old) is None


def test_a_branch_moved_since_the_decision_is_not_deleted(repo):
    decision = cli._land_leftover(repo, _STEP, cli._land_resume(repo, _STEP, None), None)
    _git(repo, "branch", "-f", _PR, _TASK)
    assert cli._land_recreate(repo, _PR, decision["old_tip"]) == f"{_PR} moved off {decision['old_tip'][:8]}"
    assert _git(repo, "rev-parse", _PR) == _git(repo, "rev-parse", _TASK)


def test_no_repo_lease_refuses_the_recreate_and_keeps_the_branch(repo, tmp_path, monkeypatch, capsys):
    old = _git(repo, "rev-parse", _PR)
    rc, ran = _land(repo, tmp_path, monkeypatch, None)  # neither granted nor refused: the store-outage path
    out = capsys.readouterr().out
    assert (rc, ran) == (2, [])
    assert "land: not recreated: no repo lease to recreate under\n" in out
    assert _git(repo, "rev-parse", _PR) == old


def test_a_remote_branch_leaves_the_refusal_unchanged(repo, monkeypatch):
    real = cli._leftover_gate_facts
    monkeypatch.setattr(cli, "_leftover_gate_facts", lambda *args: {**real(*args), "remote_exists": True})
    decision = cli._land_resume(repo, _STEP, None)
    assert cli._land_leftover(repo, _STEP, decision, None) == decision


def test_unreadable_facts_leave_the_refusal_unchanged(repo, monkeypatch):
    monkeypatch.setattr(cli, "_open_prs_for", lambda _repo, _branch, _forge=None: "forge down")
    decision = {**cli._land_resume(repo, _STEP, None)}
    assert cli._land_leftover(repo, _STEP, decision, None) == decision


def test_a_leftover_checked_out_in_a_worktree_is_not_recreated(repo, tmp_path):
    _git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), _PR)
    decision = cli._land_resume(repo, _STEP, None)
    assert cli._land_leftover(repo, _STEP, decision, None) == decision
