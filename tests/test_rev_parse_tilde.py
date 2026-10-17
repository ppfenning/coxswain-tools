import subprocess
from pathlib import Path

from agent_tools.chair_read_land_conflicts import _rev_parse


def _make_repo(parent: Path) -> tuple[Path, str]:
    repo = parent / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["config", "commit.gpgsign", "false"],
        ["commit", "-q", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
    return repo, head.stdout.strip()


def test_rev_parse_expands_leading_tilde(tmp_path, monkeypatch):
    repo, head = _make_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _rev_parse(f"~/{repo.name}", "HEAD") == head
