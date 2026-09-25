import subprocess
from pathlib import Path

import pytest

from agent_tools import cli

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-m", name)


def make_repo(tmp_path: Path, *, remote: bool) -> tuple[Path, Path]:
    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(clone)], check=True, capture_output=True)
    commit(clone, "base")
    if remote:
        git(clone, "remote", "add", "origin", str(origin))
        git(clone, "push", "origin", "main")
    return origin, clone


def advance_origin(tmp_path: Path, origin: Path, name: str) -> None:
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-b", "main", str(origin), str(other)], check=True, capture_output=True)
    commit(other, name)
    git(other, "push", "origin", "main")


@pytest.mark.parametrize("has_origin, behind, ahead, expected", [
    (False, 0, 0, "skip"),
    (True, 0, 3, "current"),
    (True, 2, 0, "fast_forward"),
    (True, 1, 1, "refuse"),
])
def test_sync_decision_row(has_origin, behind, ahead, expected):
    assert cli.sync_decision(has_origin, behind, ahead) == expected


def test_a_clone_behind_origin_is_fast_forwarded_with_head_on_main(tmp_path):
    origin, clone = make_repo(tmp_path, remote=True)
    advance_origin(tmp_path, origin, "next")
    decision, detail = cli._sync_default_branch(clone, "main")
    assert (decision, detail) == ("fast_forward", "main fast-forwarded to origin/main (1 commits)")
    assert git(clone, "rev-parse", "main") == git(origin, "rev-parse", "main")
    assert git(clone, "symbolic-ref", "--short", "HEAD") == "main"
    assert (clone / "next").exists()


def test_a_clone_on_another_branch_has_main_moved_and_head_left_alone(tmp_path):
    origin, clone = make_repo(tmp_path, remote=True)
    git(clone, "checkout", "-b", "pr/t")
    commit(clone, "task")
    head = git(clone, "rev-parse", "HEAD")
    advance_origin(tmp_path, origin, "next")
    decision, _ = cli._sync_default_branch(clone, "main")
    assert decision == "fast_forward"
    assert git(clone, "rev-parse", "main") == git(origin, "rev-parse", "main")
    assert git(clone, "symbolic-ref", "--short", "HEAD") == "pr/t"
    assert git(clone, "rev-parse", "HEAD") == head
    assert not (clone / "next").exists()


def test_a_diverged_clone_is_refused_and_neither_ref_moves(tmp_path):
    origin, clone = make_repo(tmp_path, remote=True)
    commit(clone, "local")
    advance_origin(tmp_path, origin, "remote")
    local_tip = git(clone, "rev-parse", "main")
    origin_tip = git(origin, "rev-parse", "main")
    decision, detail = cli._sync_default_branch(clone, "main")
    assert decision == "refuse"
    assert detail == "main has diverged from origin/main (1 ahead, 1 behind); reconcile it first"
    assert git(clone, "rev-parse", "main") == local_tip
    assert git(origin, "rev-parse", "main") == origin_tip


def test_a_clone_with_no_remote_is_skipped_and_runs_no_fetch(tmp_path, monkeypatch):
    _, clone = make_repo(tmp_path, remote=False)
    calls = []
    real = subprocess.run

    def recording(argv, **kwargs):
        calls.append(argv)
        return real(argv, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", recording)
    assert cli._sync_default_branch(clone, "main") == ("skip", "")
    assert not any("fetch" in argv for argv in calls)
