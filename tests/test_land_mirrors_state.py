import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_tools import cli, store_cli

APPROVED = "---\nid: t1\nstate: approved\n---\nbody\n"
DONE = "---\nid: t1\nstate: done\n---\nbody\n"
_HARNESS = Path("/h/.venv/bin/python")
_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}


def _item(tmp_path: Path, text: str = APPROVED) -> Path:
    item = tmp_path / "work" / "init" / "p1" / "t1.md"
    item.parent.mkdir(parents=True)
    item.write_text(text, encoding="utf-8")
    return item


def _recording(monkeypatch) -> list:
    seen = []
    monkeypatch.setattr(store_cli, "mirror_state", lambda *args: seen.append(args))
    return seen


def test_the_mirror_is_called_with_the_five_arguments_after_the_file_is_written(monkeypatch, tmp_path):
    item = _item(tmp_path)
    seen = []
    monkeypatch.setattr(store_cli, "mirror_state", lambda *args: seen.append((*args, item.read_text(encoding="utf-8"))))
    cli._close_approved_item(str(item), runs_dir=tmp_path / "runs", by="sess-1", merged=True)
    assert seen == [(tmp_path / "runs", "init", "t1", "done", "sess-1", DONE)]


@pytest.mark.parametrize("case, code, out, raises, warns", [
    ("exit 0", 0, '{"state": "done"}', None, False),
    ("exit 3", 3, '{"error": "no such task"}', None, True),
    ("exit 2", 2, "bad args", None, True),
    ("oserror", None, "", OSError("spawn failed"), True),
    ("missing harness", None, "", None, False),
])
def test_a_store_outcome_never_undoes_the_move(monkeypatch, tmp_path, capsys, case, code, out, raises, warns):
    item = _item(tmp_path)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")

    if case != "missing harness":
        monkeypatch.setattr(store_cli, "_harness_python", lambda: _HARNESS)
    monkeypatch.setattr(store_cli.subprocess, "run", fake_run)
    cli._close_approved_item(str(item), runs_dir=tmp_path / "runs", by="sess-1")
    printed = capsys.readouterr().out
    assert item.read_text(encoding="utf-8") == DONE
    assert ("warning: store did not record init/t1 as done" in printed) is warns
    expected = [] if case == "missing harness" else [store_cli.set_state_argv(str(_HARNESS), "init", "t1", "done", "sess-1", store_cli._store_url(tmp_path / "runs"))]
    assert calls == expected


@pytest.mark.parametrize("text, mirrored", [(DONE, True), ("---\nid: t1\nstate: blocked\n---\nbody\n", False)])
def test_an_item_already_done_is_mirrored_again_and_a_refused_one_is_not(monkeypatch, tmp_path, text, mirrored):
    item = _item(tmp_path, text)
    seen = _recording(monkeypatch)
    cli._close_approved_item(str(item), runs_dir=tmp_path / "runs", by="sess-1")
    assert (item.read_text(encoding="utf-8"), seen) == (text, [(tmp_path / "runs", "init", "t1", "done", "sess-1")] if mirrored else [])


@pytest.mark.parametrize("by, expected", [("sess-1", "sess-1"), (None, "unlabeled")])
def test_the_land_walk_mirrors_its_mark_done_under_the_record_runs_dir(monkeypatch, tmp_path, by, expected):
    record = tmp_path / "runs" / "run-1" / "tasks" / "p1" / "t1.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"task": "t1"}), encoding="utf-8")
    step = {"kind": "mark_done", "task": "t1", "path": str(record), "item": str(_item(tmp_path))}
    seen = _recording(monkeypatch)
    kwargs = {} if by is None else {"by": by}
    rc, reached, _ = cli._land_walk(tmp_path, [step], [step], None, None, "merge", False, None, [], **kwargs)
    assert (rc, reached) == (0, ["mark_done"])
    assert seen == [((tmp_path / "runs").resolve(), "init", "t1", "done", expected)]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env={**os.environ, **_GIT_ENV})


def _git_repo(tmp_path: Path, phase_branch_at: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-qb", "agents/epic-x-5/seams-task")
    (repo / "f").write_text("y")
    _git(repo, "commit", "-aqm", "Add seams module")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "branch", "epic/x/seams", phase_branch_at)
    return repo


@pytest.mark.parametrize("phase_branch_at", ["main", "agents/epic-x-5/seams-task"])
def test_recover_mirrors_done_on_a_fresh_merge_and_on_an_already_recovered_branch(monkeypatch, tmp_path, capsys, phase_branch_at):
    repo = _git_repo(tmp_path, phase_branch_at)
    record = tmp_path / "runs" / "epic-x-5" / "tasks" / "seams" / "seams-task.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"review": {"verdict": "approve"}, "proposals": [], "run_id": "epic-x-5:seams:seams-t",
                                  "ticket": "seams-task"}), encoding="utf-8")
    item = tmp_path / "work" / "x" / "seams" / "seams-task.md"
    item.parent.mkdir(parents=True)
    item.write_text("---\nid: seams-task\nstate: approved\n---\n", encoding="utf-8")
    monkeypatch.setenv("COX_SESSION_LABEL", "sess-1")
    seen = _recording(monkeypatch)
    rc = cli.main(["runs", "recover", "epic-x-5", "seams-task", "--repo", str(repo), "--runs-dir", str(tmp_path / "runs")])
    assert rc == 0, capsys.readouterr().out
    assert item.read_text(encoding="utf-8") == "---\nid: seams-task\nstate: done\n---\n"
    assert seen == [(tmp_path / "runs", "x", "seams-task", "done", "sess-1")]
