"""`route file` sets `state: ready` on a new work item, so it mirrors that state to the store.

Route sync only pushes state to GitHub Projects and never writes `state:` to a file, so it has nothing to mirror.
"""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_tools import cli, route
from agent_tools.cli import main


@pytest.fixture(autouse=True)
def _no_gh_sync_on_file(monkeypatch):
    monkeypatch.setattr("agent_tools.cli._sync_filed_items", lambda *_a, **_k: None)


def _profile(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        f"team: acme\nworkspace_dir: {ws}\nharness_dir: /opt/h\ncartridges_dir: /opt/c\nprovider_profile: /opt/p.yaml\n"
    )
    return profile, ws


def _file(profile: Path, *extra: str) -> int:
    return main(["route", "file", "--profile", str(profile), "--repo", "/repos/widget", "--title", "Fix the thing", *extra])


@pytest.fixture
def harness(monkeypatch):
    """Patch subprocess.run; `calls` records argv, `outcome` is a (code, stdout, stderr) or an exception."""
    state = SimpleNamespace(calls=[], outcome=(0, json.dumps({"state": "ready"}), ""))

    def fake_run(argv, **kwargs):
        state.calls.append(argv)
        if isinstance(state.outcome, Exception):
            raise state.outcome
        code, out, err = state.outcome
        return subprocess.CompletedProcess(argv, code, out, err)

    monkeypatch.setattr("agent_tools.store_cli._harness_python", lambda: Path("/fake/python"))
    monkeypatch.setattr(subprocess, "run", fake_run)
    return state


def test_a_filed_item_mirrors_ready_with_the_right_arguments_after_the_write(tmp_path, monkeypatch):
    profile, ws = _profile(tmp_path)
    monkeypatch.setenv("COX_SESSION_LABEL", "leader-a")
    seen = []

    def record(runs_dir, initiative, task, state, by):
        seen.append((runs_dir, initiative, task, state, by, (ws / "work/fix-the-thing/build/fix-the-thing.md").read_text()))

    monkeypatch.setattr("agent_tools.store_cli.mirror_state", record)
    assert _file(profile) == 0
    task_text = route.initiative_files("Fix the thing", "", "/repos/widget")["work/fix-the-thing/build/fix-the-thing.md"]
    assert seen == [(ws / "runs", "fix-the-thing", "fix-the-thing", "ready", "leader-a", task_text)]


def test_exit_0_mirrors_and_prints_nothing_extra(tmp_path, harness, capsys):
    profile, ws = _profile(tmp_path)
    assert _file(profile) == 0
    assert "set-state" in harness.calls[0]
    assert "warning" not in capsys.readouterr().out
    assert (ws / "work/fix-the-thing/build/fix-the-thing.md").exists()


@pytest.mark.parametrize("code", [3, 2])
def test_exit_3_and_exit_2_print_one_warning_and_the_file_still_succeeds(tmp_path, harness, capsys, code):
    profile, ws = _profile(tmp_path)
    harness.outcome = (code, "", "nope")
    assert _file(profile) == 0
    warnings = [line for line in capsys.readouterr().out.splitlines() if line.startswith("warning:")]
    assert len(warnings) == 1
    assert "fix-the-thing/fix-the-thing as ready" in warnings[0]
    assert (ws / "work/fix-the-thing/build/fix-the-thing.md").exists()


def test_a_missing_harness_prints_nothing_extra_and_the_file_succeeds(tmp_path, monkeypatch, capsys):
    profile, ws = _profile(tmp_path)
    monkeypatch.setattr("agent_tools.store_cli._harness_python", lambda: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no harness, no subprocess"))
    assert _file(profile) == 0
    assert "warning" not in capsys.readouterr().out
    assert (ws / "work/fix-the-thing/build/fix-the-thing.md").exists()


def test_an_intake_file_sets_no_state_and_mirrors_nothing(tmp_path, harness):
    profile, _ = _profile(tmp_path)
    assert _file(profile, "--intake") == 0
    assert harness.calls == []


def test_two_work_items_in_one_write_mirror_twice_and_one_warning_hides_nothing(tmp_path, harness, capsys):
    _, ws = _profile(tmp_path)
    harness.outcome = (2, "", "boom")
    mapping = {**route.initiative_files("One", "", "/r"), **route.initiative_files("Two", "", "/r")}
    assert cli._write_mapping(mapping, ws, by="me") is None
    tasks = [argv[argv.index("set-state") + 1 : argv.index("set-state") + 4] for argv in harness.calls]
    assert tasks == [["one", "one", "ready"], ["two", "two", "ready"]]
    assert len([line for line in capsys.readouterr().out.splitlines() if line.startswith("warning:")]) == 2
