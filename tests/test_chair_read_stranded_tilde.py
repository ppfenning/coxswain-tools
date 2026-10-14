import subprocess

from agent_tools.chair_read_stranded import _branch_exists, read_missing_repos


def _record(repo: str) -> dict:
    return {
        "run": "r1",
        "task": "r1",
        "phase": "p1",
        "branch": "b1",
        "repo": repo,
        "review": {"verdict": "approve"},
        "arbitration": "arbiter: skipped (both approved)",
        "landed": False,
    }


ITEMS = [{"id": "r1", "state": "ready"}]


def test_a_tilde_repo_that_exists_under_home_is_not_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "proj").mkdir()
    assert read_missing_repos([_record("~/proj")], ITEMS) == []


def test_a_tilde_repo_that_does_not_exist_is_listed_as_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert read_missing_repos([_record("~/gone")], ITEMS) == ["~/gone"]


def test_the_default_branch_check_expands_a_tilde_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    subprocess.run([*git, "branch", "epic/i/p1"], check=True)
    assert _branch_exists("~/repo", "epic/i/p1") is True
    assert _branch_exists("~/repo", "epic/i/other") is False
