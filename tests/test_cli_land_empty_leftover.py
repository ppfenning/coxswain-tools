"""The land edge recreates an empty local leftover pr branch from main without touching the remote, and refuses a
leftover that has commits not on main or an open pull request, naming which."""

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
_LEASE = store_cli.LeaseGranted(1, "me")


def _git(repo, *args):
    return sp.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_ENV).stdout.strip()


def _has_ref(repo, ref):
    return sp.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", ref], capture_output=True).returncode == 0


@pytest.fixture(autouse=True)
def no_open_prs(monkeypatch):
    monkeypatch.setattr(cli, "_open_prs_for", lambda _repo, _branch, _forge=None: [])


@pytest.fixture
def origin(tmp_path):
    return tmp_path / "origin.git"


@pytest.fixture
def repo(tmp_path, origin):
    """main and a one-commit task branch on a real bare origin, and a local-only leftover `pr/seams-task` at main."""
    root = tmp_path / "repo"
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
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "origin", "main", _TASK)
    _git(root, "branch", _PR, "main")
    return root


def _remote_refs(origin):
    return _git(origin, "for-each-ref", "--format=%(refname) %(objectname)")


def _land(repo, tmp_path, monkeypatch, prs=()):
    """`cox runs land --apply` with the steps faked; returns rc, the step kinds run."""
    task_dir = tmp_path / "runs/epic-x-5/tasks/seams"
    task_dir.mkdir(parents=True)
    (task_dir / "seams-task.json").write_text(json.dumps(_record()), encoding="utf-8")
    (tmp_path / "runs/policy.tracker.json").write_text('{"tracker": "github-projects"}', encoding="utf-8")
    ran = []
    monkeypatch.setattr(cli, "_execute_land_step", lambda _repo, step, _forge=None: (ran.append(step["kind"]) or True, "https://x/pull/7"))
    monkeypatch.setattr(cli, "_open_prs_for", lambda _repo, _branch, _forge=None: list(prs))
    monkeypatch.setattr(land_repo_lease, "acquire", lambda *_args: _LEASE)
    monkeypatch.setattr(land_repo_lease, "release", lambda *_args: None)
    monkeypatch.setattr(land_repo_lease, "renew", lambda *_args: _LEASE)
    rc = cli.main(["runs", "land", "epic-x-5", "--repo", str(repo), "--apply", "--runs-dir", str(tmp_path / "runs"), "--gate", "full"])
    return rc, ran


def test_an_empty_local_leftover_is_recreated_and_the_land_continues_with_the_remote_untouched(repo, origin, tmp_path, monkeypatch, capsys):
    before = _remote_refs(origin)
    rc, ran = _land(repo, tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"land: recreating empty leftover {_PR} from main\n" in out and "backed up" not in out
    assert ran[:10] == _PRESYNCED
    assert not _has_ref(repo, f"refs/backup/{_PR}") and not _has_ref(repo, f"refs/heads/{_PR}")
    assert _remote_refs(origin) == before


def test_the_recreated_branch_carries_the_cherry_picked_tree(repo):
    gated = cli._leftover_gated(repo, _STEP, cli._land_resume(repo, _STEP, None), None)
    assert gated["kind"] == "fresh" and gated["old_tip"] == _git(repo, "rev-parse", _PR)
    assert cli._land_recreate(repo, _PR, gated["old_tip"]) is None
    ok, detail = cli._execute_land_step(repo, _STEP)
    assert ok, detail
    assert _git(repo, "rev-parse", f"{_PR}^{{tree}}") == _git(repo, "rev-parse", f"{_TASK}^{{tree}}")


def test_a_failure_to_compute_the_cherry_picked_tree_does_not_abort_a_recreate(repo, tmp_path, monkeypatch, capsys):
    cannot = {"kind": "refuse", "reason": f"cannot compute the cherry-picked tree of {_TASK} onto main"}
    monkeypatch.setattr(cli, "_land_resume", lambda _repo, _step, _forge=None: cannot)
    rc, ran = _land(repo, tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "cannot compute" not in out and ran[:10] == _PRESYNCED


def test_a_branch_with_a_commit_not_on_main_is_refused_naming_it(repo, origin, tmp_path, monkeypatch, capsys):
    _git(repo, "checkout", "-q", _PR)
    (repo / "g").write_text("z")
    _git(repo, "add", "g")
    _git(repo, "commit", "-qm", "stray")
    _git(repo, "checkout", "-q", "main")
    tip, before = _git(repo, "rev-parse", _PR), _remote_refs(origin)
    rc, ran = _land(repo, tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert (rc, ran) == (2, [])
    assert f"land: refusing, branch {_PR} already exists in {repo}: {_PR} has 1 commits not on main\n" in out
    assert _git(repo, "rev-parse", _PR) == tip and _remote_refs(origin) == before


def test_a_branch_behind_an_open_pull_request_is_refused_naming_it(repo, origin, tmp_path, monkeypatch, capsys):
    tip, before = _git(repo, "rev-parse", _PR), _remote_refs(origin)
    rc, ran = _land(repo, tmp_path, monkeypatch, prs=[7])
    out = capsys.readouterr().out
    assert (rc, ran) == (2, [])
    assert f"{_PR} has open pull request #7\n" in out
    assert _git(repo, "rev-parse", _PR) == tip and _remote_refs(origin) == before


def test_a_pushed_branch_with_an_open_pull_request_is_still_adopted(repo):
    _git(repo, "push", "-q", "origin", _PR)
    assert cli._leftover_gated(repo, _STEP, {"kind": "adopt", "pr": 7}, None) is None


def test_the_gate_reads_its_facts_from_the_edge_helpers(repo, monkeypatch):
    decision = cli._land_resume(repo, _STEP, None)
    monkeypatch.setattr(cli, "_unique_commit_count", lambda _repo, _base, _tip: 3)
    assert cli._leftover_gated(repo, _STEP, decision, None)["reason"] == f"{_PR} has 3 commits not on main"
    monkeypatch.setattr(cli, "_unique_commit_count", lambda _repo, _base, _tip: 0)
    monkeypatch.setattr(cli, "_remote_branch_exists", lambda _repo, _branch: True)
    assert cli._leftover_gated(repo, _STEP, decision, None) is None


def test_a_rerun_after_the_branch_was_recreated_proceeds_as_a_fresh_land(repo, origin, tmp_path, monkeypatch, capsys):
    old = _git(repo, "rev-parse", _PR)
    assert cli._land_recreate(repo, _PR, old) is None
    assert cli._land_recreate(repo, _PR, old) is None
    before = _remote_refs(origin)
    rc, ran = _land(repo, tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "recreating" not in out and "refusing" not in out
    assert ran[:10] == _PRESYNCED
    assert _remote_refs(origin) == before
