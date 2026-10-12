import pytest

from agent_tools import route_edit, run_store

TICKET_FILE = "---\nstate: todo\ntitle: Old title\nneeds:\n- other\nsurfaces:\n- old.py\nid: t1\nphase: p1\nrepo: r\n---\n\nOld body\n"
INTAKE = "---\nid: fix-it\ntitle: Old title\nrepo: r\n---\n\nOld body\n"


def _ticket_row():
    return {
        "kind": "task", "initiative": "ship", "task_id": "t1", "phase": "p1", "state": "todo", "needs": ["other"],
        "title": "Old title", "surfaces": ["old.py"], "body": "\nOld body\n", "extra": {"id": "t1", "phase": "p1", "repo": "r"},
        "holder": None, "epoch": None, "expires_at": None,
    }


def _intake_row():
    return {"kind": "intake", "initiative": "intake", "task_id": "fix-it", "state": "queued", "extra": {}}


def _store(monkeypatch, rows, reason=""):
    """Patch the store edge. Returns the rows the store took and the hashes `set_export_hash` recorded."""
    upserts, hashes = [], []
    monkeypatch.setattr(run_store, "read_queue", lambda runs_dir, initiative=None, kind=None: rows)
    monkeypatch.setattr(run_store, "upsert_row_detail", lambda runs_dir, row: upserts.append(row) or reason)
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: upserts.append(row) or True)
    monkeypatch.setattr(run_store, "export_hashes", lambda runs_dir: {("ship", "t1"): "stale"})
    monkeypatch.setattr(
        run_store, "set_export_hash", lambda runs_dir, initiative, task_id, digest: hashes.append((initiative, task_id, digest)) or True
    )
    return upserts, hashes


def _file(tmp_path, relative, text):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_four_flag_edit_changes_the_store_row_and_the_file_together(tmp_path, monkeypatch):
    upserts, hashes = _store(monkeypatch, [_ticket_row()])
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    body_file = _file(tmp_path, "body.txt", "New body\n")
    result = route_edit.apply_edit(
        tmp_path, tmp_path, "t1", title="New title", body_file=str(body_file), surfaces="a.py, b/c.py", state="ready", live_runs=()
    )
    assert result.code == route_edit.DONE
    [row] = upserts
    assert (row["title"], row["body"], row["surfaces"], row["state"]) == ("New title", "\nNew body\n", ["a.py", "b/c.py"], "ready")
    untouched = ("kind", "initiative", "task_id", "phase", "needs", "extra", "holder", "epoch", "expires_at")
    assert {key: row[key] for key in untouched} == {key: _ticket_row()[key] for key in untouched}
    assert path.read_text(encoding="utf-8") == (
        "---\nstate: ready\ntitle: New title\nneeds:\n- other\nsurfaces:\n- a.py\n- b/c.py\nid: t1\nphase: p1\nrepo: r\n---\n\nNew body\n"
    )
    assert "+state: ready" in result.text and "+title: New title" in result.text
    assert [(initiative, task_id) for initiative, task_id, _ in hashes] == [("ship", "t1")]


def test_a_hand_edited_file_is_overwritten_by_the_store_text(tmp_path, monkeypatch):
    upserts, hashes = _store(monkeypatch, [_ticket_row()])
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE.replace("Old body", "Hand edit"))
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", state="ready", live_runs=())
    assert result.code == route_edit.DONE and "-Hand edit" in result.text
    assert path.read_text(encoding="utf-8") == TICKET_FILE.replace("state: todo", "state: ready")
    assert [row["state"] for row in upserts] == ["ready"] and len(hashes) == 1


def test_an_invalid_state_changes_nothing(tmp_path, monkeypatch):
    upserts, hashes = _store(monkeypatch, [_ticket_row()])
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", title="New title", state="finished", live_runs=())
    assert result.code == route_edit.REFUSED
    assert "invalid state 'finished'" in result.text and "ready" in result.text
    assert path.read_text(encoding="utf-8") == TICKET_FILE
    assert upserts == [] and hashes == []


def test_a_store_that_refuses_the_row_leaves_the_file_alone(tmp_path, monkeypatch):
    _, hashes = _store(monkeypatch, [_ticket_row()], reason="exit 3")
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", title="New title", live_runs=())
    assert result.code == route_edit.FAILED and "exit 3" in result.text
    assert path.read_text(encoding="utf-8") == TICKET_FILE and hashes == []


def test_dry_run_on_a_task_ticket_writes_nothing(tmp_path, monkeypatch):
    upserts, hashes = _store(monkeypatch, [_ticket_row()])
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", state="ready", dry_run=True, live_runs=())
    assert result.code == route_edit.DONE and "+state: ready" in result.text
    assert path.read_text(encoding="utf-8") == TICKET_FILE and upserts == [] and hashes == []


def test_surfaces_and_state_are_for_a_task_ticket_only(tmp_path, monkeypatch):
    upserts, _ = _store(monkeypatch, [_intake_row()])
    path = _file(tmp_path, "intake/fix-it.md", INTAKE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "fix-it", state="ready", live_runs=())
    assert result.code == route_edit.REFUSED and "task ticket only" in result.text
    assert path.read_text(encoding="utf-8") == INTAKE and upserts == []


def test_an_unknown_id_keeps_its_error(tmp_path, monkeypatch):
    _store(monkeypatch, [_ticket_row()])
    result = route_edit.apply_edit(tmp_path, tmp_path, "nope", title="New", live_runs=())
    assert (result.code, result.text) == (route_edit.REFUSED, "no queued intake or initiative named 'nope'")


def test_a_task_id_in_two_initiatives_is_refused(tmp_path, monkeypatch):
    upserts, _ = _store(monkeypatch, [_ticket_row(), {**_ticket_row(), "initiative": "other"}])
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", title="New", live_runs=())
    assert result.code == route_edit.REFUSED and "several initiatives" in result.text and upserts == []


def test_an_intake_edit_still_rewrites_the_file_and_the_row(tmp_path, monkeypatch):
    upserts, _ = _store(monkeypatch, [_intake_row()])
    path = _file(tmp_path, "intake/fix-it.md", INTAKE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "fix-it", title="New title", live_runs=())
    assert result.code == route_edit.DONE
    assert path.read_text(encoding="utf-8") == INTAKE.replace("title: Old title", "title: New title")
    assert [row["title"] for row in upserts] == ["New title"]


@pytest.mark.parametrize(
    ("state", "surfaces", "expected"),
    [
        (None, None, None),
        ("ready", "a.py", None),
        ("dropped", None, None),
        ("finished", None, "invalid state 'finished'"),
        (None, " , ", "--surfaces must name at least one path"),
    ],
)
def test_check_task_options(state, surfaces, expected):
    reason = route_edit.check_task_options(state, surfaces)
    assert reason is None if expected is None else expected in reason


def test_plan_task_row_changes_only_the_named_fields():
    row = _ticket_row()
    assert route_edit.plan_task_row(row) == row
    assert route_edit.plan_task_row(row, surfaces="x.py,,y.py ") == {**row, "surfaces": ["x.py", "y.py"]}
    assert row == _ticket_row()
