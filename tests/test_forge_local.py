import subprocess
from pathlib import Path

from agent_tools import forge_local

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
STEP = {"branch": "pr/t", "default_branch": "main", "subject": "t"}


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
    git(clone, "checkout", "-b", "pr/t")
    commit(clone, "task")
    return origin, clone


def test_merge_fast_forwards_main_pushes_to_origin_and_deletes_the_branch(tmp_path):
    origin, clone = make_repo(tmp_path, remote=True)
    tip = git(clone, "rev-parse", "pr/t")
    ok, detail = forge_local.merge(clone, STEP)
    assert (ok, detail) == (True, f"merged pr/t into main at {tip[:7]}")
    assert git(origin, "rev-parse", "main") == tip
    assert git(clone, "rev-parse", "main") == tip
    assert git(clone, "branch", "--list", "pr/t") == ""


def test_merge_without_a_remote_merges_and_skips_the_push(tmp_path, monkeypatch):
    _, clone = make_repo(tmp_path, remote=False)
    calls = []
    real = subprocess.run

    def recording(argv, **kwargs):
        calls.append(argv)
        return real(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording)
    ok, detail = forge_local.merge(clone, STEP)
    assert ok and detail.startswith("merged pr/t into main at ")
    assert not [c for c in calls if "push" in c]
    monkeypatch.undo()
    assert git(clone, "branch", "--list", "pr/t") == ""


def test_a_diverged_main_returns_false_and_keeps_the_branch(tmp_path):
    _, clone = make_repo(tmp_path, remote=True)
    git(clone, "checkout", "main")
    commit(clone, "other")
    git(clone, "checkout", "pr/t")
    ok, detail = forge_local.merge(clone, STEP)
    assert ok is False and detail
    assert git(clone, "branch", "--list", "pr/t") != ""


def test_a_failed_push_returns_false_and_keeps_the_branch(tmp_path):
    _, clone = make_repo(tmp_path, remote=True)
    git(clone, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    ok, detail = forge_local.merge(clone, STEP)
    assert ok is False and detail
    assert git(clone, "branch", "--list", "pr/t") != ""


def test_push_open_pr_and_wait_checks_succeed_and_run_no_git(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    assert forge_local.push(tmp_path, "pr/t") == (True, "local forge: pr/t stays local until it merges")
    assert forge_local.open_pr(tmp_path, "t", "b") == (True, "local forge: no pull request")
    assert forge_local.wait_checks(tmp_path, 1.0) == (True, "local forge: the land's own checks are the gate")
    assert calls == []


def test_find_open_prs_is_empty(tmp_path):
    assert forge_local.find_open_prs(tmp_path, "pr/t") == []
