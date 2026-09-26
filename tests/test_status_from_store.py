import pytest

from agent_tools import route, run_store
from agent_tools.cli import main


def _item(task: str, state: str) -> dict:
    return {"id": task, "initiative": "x", "phase": "p1", "state": state, "needs": [], "file": f"p1/{task}.md"}


def _row(task: str, state: str) -> dict:
    return {"initiative": "x", "task_id": task, "state": state}


def test_the_store_overrides_a_differing_file_state():
    assert route.with_store_states([_item("t", "ready")], [_row("t", "done")], "store") == [_item("t", "done")]


def test_a_task_with_no_store_row_keeps_its_file_state():
    assert route.with_store_states([_item("t", "ready")], [_row("other", "done")], "store") == [_item("t", "ready")]


def test_files_mode_ignores_the_store_rows():
    assert route.with_store_states([_item("t", "ready")], [_row("t", "done")], "files") == [_item("t", "ready")]


def test_the_input_items_are_not_mutated():
    items = [_item("t", "ready")]
    route.with_store_states(items, [_row("t", "done")], "store")
    assert items == [_item("t", "ready")]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "runs").mkdir(parents=True)
    task = ws / "work" / "x" / "p1" / "t.md"
    task.parent.mkdir(parents=True)
    task.write_text("---\nid: t\nstate: ready\n---\n\nBody\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"team: acme\nworkspace_dir: {ws}\nprovider_profile: {tmp_path / 'provider.yaml'}\n")
    monkeypatch.setattr(run_store, "work_items", lambda runs_dir, initiative=None: [_row("t", "bogus")])
    return profile


def _provider(profile, mode: str) -> None:
    profile.parent.joinpath("provider.yaml").write_text(f"work_state: {mode}\n")


def test_the_docket_shows_the_stores_state(workspace, capsys):
    _provider(workspace, "store")
    assert main(["route", "context", "--profile", str(workspace)]) == 0
    assert "problem: x: p1/t.md: unknown state 'bogus'" in capsys.readouterr().out


def test_route_status_shows_the_stores_state(workspace, capsys):
    _provider(workspace, "store")
    assert main(["route", "status", "--profile", str(workspace)]) == 0
    assert "problem: x: p1/t.md: unknown state 'bogus'" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["context", "status"])
def test_files_mode_shows_the_file_state_and_reads_no_store(workspace, capsys, monkeypatch, command):
    _provider(workspace, "files")
    monkeypatch.setattr(run_store, "work_items", lambda *a, **k: pytest.fail("the store was read under files mode"))
    assert main(["route", command, "--profile", str(workspace)]) == 0
    assert "problem:" not in capsys.readouterr().out
