import subprocess
from pathlib import Path

import pytest

from agent_tools import cli


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")


@pytest.fixture
def clone(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace cloned from a bare upstream, with one pushed commit holding work/seed.md."""
    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    workspace = tmp_path / "workspace"
    subprocess.run(["git", "clone", "-q", str(bare), str(workspace)], check=True, capture_output=True)
    _identify(workspace)
    (workspace / "work").mkdir()
    (workspace / "work" / "seed.md").write_text("seed\n")
    (workspace / "runs").mkdir()
    _git(workspace, "add", "work")
    _git(workspace, "commit", "-q", "-m", "seed")
    _git(workspace, "push", "-q", "-u", "origin", "HEAD:main")
    return bare, workspace


def _stub_export(monkeypatch: pytest.MonkeyPatch, text: str | None) -> None:
    def export(runs_dir: Path, workspace: Path) -> list[str]:
        if text is not None:
            (workspace / "work" / "ticket.md").write_text(text)
        return []

    monkeypatch.setattr(cli, "_export_board", export)


def test_a_committing_export_pushes_the_commit_to_the_upstream(clone, monkeypatch) -> None:
    bare, workspace = clone
    _stub_export(monkeypatch, "changed\n")
    assert cli._chair_export_hook(workspace / "runs", ("t1",)) == "export: committed 1 row(s)"
    assert _git(bare, "rev-parse", "main") == _git(workspace, "rev-parse", "HEAD")
    assert _git(bare, "log", "-1", "--format=%s", "main") == "chair: export 1 changed row(s)"


def test_a_rejected_push_is_a_note_and_does_not_raise(clone, monkeypatch, tmp_path) -> None:
    bare, workspace = clone
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True, capture_output=True)
    _identify(other)
    (other / "work" / "elsewhere.md").write_text("e\n")
    _git(other, "add", "work")
    _git(other, "commit", "-q", "-m", "elsewhere")
    _git(other, "push", "-q", "origin", "HEAD:main")
    advanced = _git(bare, "rev-parse", "main")
    _stub_export(monkeypatch, "changed\n")
    note = cli._chair_export_hook(workspace / "runs", ("t1",))
    assert note.startswith("export: committed 1 row(s); workspace push failed:")
    assert _git(bare, "rev-parse", "main") == advanced


def test_an_export_with_no_file_changes_does_not_push(clone, monkeypatch) -> None:
    bare, workspace = clone
    before = _git(bare, "rev-parse", "main")
    _stub_export(monkeypatch, None)
    assert cli._chair_export_hook(workspace / "runs", ("t1",)) == "export: no file changes"
    assert _git(bare, "rev-parse", "main") == before


def test_a_checkout_with_no_upstream_leaves_the_note_unchanged(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "solo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(workspace)], check=True)
    _identify(workspace)
    (workspace / "work").mkdir()
    (workspace / "runs").mkdir()
    _stub_export(monkeypatch, "changed\n")
    assert cli._chair_export_hook(workspace / "runs", ("t1",)) == "export: committed 1 row(s)"


def test_a_live_run_sets_pull_workspace_and_a_dry_run_leaves_it_none(clone) -> None:
    _, workspace = clone
    args = (workspace / "runs", {}, "chair", 1, "h")
    live = cli._chair_run_deps(*args, False, print, workspace / "p.yaml", "files")
    dry = cli._chair_run_deps(*args, True, print, workspace / "p.yaml", "files")
    assert live.pull_workspace is not None
    assert live.pull_workspace() == ""
    assert dry.pull_workspace is None
