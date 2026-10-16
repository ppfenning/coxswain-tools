from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_tools import workspace_sync
from agent_tools.workspace_sync import failure_line, parse_upstream, probe_line, pull_workspace, push_workspace


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def record_git(monkeypatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    real = workspace_sync._git

    def recording(workspace: Path, *args: str) -> tuple[int, str, str]:
        calls.append(args)
        return real(workspace, *args)

    monkeypatch.setattr(workspace_sync, "_git", recording)
    return calls


def ran_sync(calls: list[tuple[str, ...]]) -> bool:
    return any(args[0] in ("pull", "push") for args in calls)


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A bare upstream with one commit, and two clones of it."""
    upstream = tmp_path / "upstream.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(upstream))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", str(upstream), str(seed))
    git(seed, "checkout", "-b", "main")
    commit(seed, "first")
    git(seed, "push", "origin", "main")
    one, two = tmp_path / "one", tmp_path / "two"
    for clone in (one, two):
        git(tmp_path, "clone", "-b", "main", str(upstream), str(clone))
    return upstream, one, two


def test_pull_fast_forwards_a_clone_behind_its_upstream(repos):
    upstream, one, two = repos
    new = commit(two, "second")
    git(two, "push", "origin", "main")
    assert pull_workspace(one) == ""
    assert git(one, "rev-parse", "HEAD") == new


def test_pull_reports_diverged_history_and_leaves_head(repos):
    upstream, one, two = repos
    commit(two, "theirs")
    git(two, "push", "origin", "main")
    mine = commit(one, "mine")
    line = pull_workspace(one)
    assert line.startswith("workspace pull failed:")
    assert "\n" not in line
    assert git(one, "rev-parse", "HEAD") == mine


@pytest.mark.parametrize("rebase", ["true", "false"])
def test_pull_carries_unstaged_and_untracked_changes_through(repos, rebase):
    upstream, one, two = repos
    git(one, "config", "pull.rebase", rebase)
    (one / "first").write_text("dirty")
    (one / "scratch").write_text("untracked")
    new = commit(two, "second")
    git(two, "push", "origin", "main")
    assert pull_workspace(one) == ""
    assert git(one, "rev-parse", "HEAD") == new
    assert (one / "first").read_text() == "dirty"
    assert (one / "scratch").read_text() == "untracked"
    assert git(one, "stash", "list") == ""


def test_pull_with_dirty_files_reports_diverged_history_and_leaves_them(repos):
    upstream, one, two = repos
    git(one, "config", "pull.rebase", "true")
    commit(two, "theirs")
    git(two, "push", "origin", "main")
    mine = commit(one, "mine")
    (one / "first").write_text("dirty")
    (one / "scratch").write_text("untracked")
    line = pull_workspace(one)
    assert line.startswith("workspace pull failed:")
    assert "\n" not in line
    assert git(one, "rev-parse", "HEAD") == mine
    assert (one / "first").read_text() == "dirty"
    assert (one / "scratch").read_text() == "untracked"
    assert git(one, "stash", "list") == ""


def test_pull_undoes_a_stash_conflict(repos):
    upstream, one, two = repos
    git(one, "config", "pull.rebase", "true")
    before = git(one, "rev-parse", "HEAD")
    (two / "first").write_text("theirs")
    git(two, "commit", "-am", "theirs")
    git(two, "push", "origin", "main")
    (one / "first").write_text("mine")
    line = pull_workspace(one)
    assert line.startswith("workspace pull failed:")
    assert "conflicted" in line
    assert "\n" not in line
    assert git(one, "rev-parse", "HEAD") == before
    assert (one / "first").read_text() == "mine"
    assert git(one, "diff", "--name-only", "--diff-filter=U") == ""
    assert git(one, "stash", "list") == ""


def test_pull_fast_forwards_a_clean_tree_with_rebase_configured(repos):
    upstream, one, two = repos
    git(one, "config", "pull.rebase", "true")
    new = commit(two, "second")
    git(two, "push", "origin", "main")
    assert pull_workspace(one) == ""
    assert git(one, "rev-parse", "HEAD") == new


def test_plain_directory_is_a_noop_that_runs_no_pull(tmp_path, monkeypatch):
    calls = record_git(monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert pull_workspace(plain) == ""
    assert push_workspace(plain) == ""
    assert not ran_sync(calls)


def test_missing_directory_returns_empty(tmp_path):
    assert pull_workspace(tmp_path / "absent") == ""


def test_subdirectory_of_a_checkout_is_not_a_checkout(repos, monkeypatch):
    upstream, one, two = repos
    commit(two, "second")
    git(two, "push", "origin", "main")
    head = git(one, "rev-parse", "HEAD")
    sub = one / "sub"
    sub.mkdir()
    calls = record_git(monkeypatch)
    assert pull_workspace(sub) == ""
    assert push_workspace(sub) == ""
    assert not ran_sync(calls)
    assert git(one, "rev-parse", "HEAD") == head


def test_checkout_without_upstream_is_a_noop_that_runs_no_pull(tmp_path, monkeypatch):
    repo = tmp_path / "solo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    head = commit(repo, "only")
    calls = record_git(monkeypatch)
    assert pull_workspace(repo) == ""
    assert push_workspace(repo) == ""
    assert not ran_sync(calls)
    assert git(repo, "rev-parse", "HEAD") == head


def test_push_sends_a_commit_ahead_to_the_upstream(repos):
    upstream, one, _ = repos
    new = commit(one, "ahead")
    assert push_workspace(one) == ""
    assert git(upstream, "rev-parse", "main") == new


def test_push_reports_a_non_fast_forward_rejection(repos):
    upstream, one, two = repos
    theirs = commit(two, "theirs")
    git(two, "push", "origin", "main")
    commit(one, "mine")
    line = push_workspace(one)
    assert line.startswith("workspace push failed:")
    assert "\n" not in line
    assert git(upstream, "rev-parse", "main") == theirs


def test_a_hung_git_is_a_timeout_line_even_in_the_probe(repos, monkeypatch):
    _, one, _ = repos

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(workspace_sync.subprocess, "run", hang)
    assert pull_workspace(one) == "workspace pull failed: git timed out after 30s"
    assert push_workspace(one) == "workspace push failed: git timed out after 30s"


def test_an_unrunnable_git_is_a_failure_line(repos, monkeypatch):
    _, one, _ = repos

    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(workspace_sync.subprocess, "run", missing)
    assert pull_workspace(one).startswith("workspace pull failed: cannot run git:")
    assert push_workspace(one).startswith("workspace push failed: cannot run git:")


def test_failure_line_is_empty_on_success():
    assert failure_line("pull", 0, "ignored") == ""


def test_failure_line_takes_the_first_stderr_line():
    assert failure_line("pull", 1, "\nboom\nfatal: more") == "workspace pull failed: boom"


def test_failure_line_names_the_exit_code_when_stderr_is_blank():
    assert failure_line("push", 3, "\n") == "workspace push failed: exit code 3"


def test_probe_line_separates_no_op_from_failure():
    assert probe_line("pull", 0, "/w\n", "", "/w") is None
    assert probe_line("pull", 0, "/w\n", "", "/w/sub") == ""
    missing = "fatal: not a git repository (or any of the parent directories): .git"
    assert probe_line("pull", 128, "", missing, "/w") == ""
    dubious = "fatal: detected dubious ownership in repository at '/w'"
    assert probe_line("pull", 128, "", dubious, "/w") == f"workspace pull failed: {dubious}"


def test_parse_upstream_reads_the_current_branch_only():
    listing = " \torigin\trefs/heads/other\n*\torigin\trefs/heads/main\n"
    assert parse_upstream(listing) == ("origin", "refs/heads/main")
    assert parse_upstream("*\t\t\n \torigin\trefs/heads/x\n") is None
    assert parse_upstream("") is None
