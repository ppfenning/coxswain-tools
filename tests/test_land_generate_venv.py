import os
import subprocess

from agent_tools import cli

_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_ENV).stdout


def test_generate_amends_with_an_ignored_linked_venv(tmp_path, monkeypatch):
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    (wt / ".gitignore").write_text(".venv\n")
    (wt / ".agent-generate").write_text("echo regenerated > README.md\n")
    (wt / "README.md").write_text("old\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "task")
    (tmp_path / "venv").mkdir()
    (wt / ".venv").symlink_to(tmp_path / "venv")
    assert cli._generate_and_amend(wt, None, echo=lambda _: None) == (True, "")
    assert _git(wt, "show", "--name-only", "--format=", "HEAD").split() == [".agent-generate", ".gitignore", "README.md"]
