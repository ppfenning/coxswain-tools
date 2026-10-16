import pytest

from agent_tools import run_store
from agent_tools.cli import main

_ROW = {"kind": "task", "state": "todo", "title": "fix the thing", "issue_number": 7}


@pytest.fixture(autouse=True)
def _store(monkeypatch):
    """`route file` syncs each item it writes and upserts a row; nothing here may reach `gh` or a real store."""
    monkeypatch.setattr("agent_tools.cli._sync_filed_items", lambda *_a, **_k: None)
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: True)
    monkeypatch.setattr(run_store, "read_queue", lambda runs_dir, *_a, **_k: [_ROW])


def _profile(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "team: acme\n"
        f"workspace_dir: {ws}\n"
        "harness_dir: /opt/coxswain-graphs\n"
        "cartridges_dir: /opt/cartridges\n"
        "provider_profile: /opt/providers/acme.yaml\n"
    )
    return profile, ws


def _file_intake(profile, title, *extra):
    return main(
        ["route", "file", "--profile", str(profile), "--repo", "/repos/widget", "--title", title, "--intake", *extra]
    )


def _intake_files(ws):
    return list((ws / "intake").glob("*.md"))


def test_a_title_matching_an_open_issue_is_refused_and_writes_nothing(tmp_path, capsys):
    profile, ws = _profile(tmp_path)

    rc = _file_intake(profile, "Fix: the Thing")

    assert rc == 2
    assert _intake_files(ws) == []
    assert (
        "routing: refusing Fix: the Thing: duplicate of issue 7; pass --force to file it anyway"
        in capsys.readouterr().out
    )


def test_force_files_the_duplicate_anyway(tmp_path):
    profile, ws = _profile(tmp_path)

    rc = _file_intake(profile, "Fix: the Thing", "--force")

    assert rc == 0
    assert len(_intake_files(ws)) == 1


def test_an_unrelated_title_is_filed(tmp_path):
    profile, ws = _profile(tmp_path)

    rc = _file_intake(profile, "Something unrelated")

    assert rc == 0
    assert len(_intake_files(ws)) == 1


@pytest.mark.parametrize("state", ["done", "dropped", "landed"])
def test_a_finished_row_is_not_an_open_issue(tmp_path, monkeypatch, state):
    profile, ws = _profile(tmp_path)
    monkeypatch.setattr(run_store, "read_queue", lambda runs_dir, *_a, **_k: [{**_ROW, "state": state}])

    rc = _file_intake(profile, "Fix: the Thing")

    assert rc == 0
    assert len(_intake_files(ws)) == 1


@pytest.mark.parametrize("state", ["todo", "ready", "in_progress", "blocked"])
def test_an_unfinished_row_still_counts_as_open(tmp_path, monkeypatch, state):
    profile, ws = _profile(tmp_path)
    monkeypatch.setattr(run_store, "read_queue", lambda runs_dir, *_a, **_k: [{**_ROW, "state": state}])

    assert _file_intake(profile, "Fix: the Thing") == 2
    assert _intake_files(ws) == []


def test_an_unreadable_store_gives_no_duplicate_and_the_file_is_written(tmp_path, monkeypatch):
    profile, ws = _profile(tmp_path)
    monkeypatch.setattr(run_store, "read_queue", lambda runs_dir, *_a, **_k: [])

    rc = _file_intake(profile, "Fix: the Thing")

    assert rc == 0
    assert len(_intake_files(ws)) == 1
