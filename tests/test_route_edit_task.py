import pytest

from agent_tools import route_edit, run_store, store_cli

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


class _Fake:
    """A store double: `rows` is what it holds. An upsert merges the keys it carries, as `harness.store_queue.upsert`
    does; set-state is a compare-and-set on `expected`. `upserts`, `states` and `hashes` record every write."""

    def __init__(self, rows, reason=""):
        self.rows, self.reason = [dict(row) for row in rows], reason
        self.upserts, self.states, self.hashes = [], [], []
        self.on_read = None  # called after a read hands back its snapshot: a write some other process makes meanwhile

    def read_queue(self, runs_dir, initiative=None, kind=None):
        snapshot = [dict(row) for row in self.rows if initiative in (None, row.get("initiative"))]
        if self.on_read is not None:
            hook, self.on_read = self.on_read, None
            hook(self)
        return snapshot

    def _row(self, initiative, task_id):
        return next(row for row in self.rows if (row["initiative"], row["task_id"]) == (initiative, task_id))

    def upsert_row_detail(self, runs_dir, row):
        self.upserts.append(dict(row))
        if not self.reason:
            self._row(row["initiative"], row["task_id"]).update(row)
        return self.reason

    def set_state(self, runs_dir, initiative, task, state, by, expected=None):
        self.states.append((initiative, task, state, by, expected))
        row = self._row(initiative, task)
        if expected is not None and row["state"] != expected:
            return store_cli.StateRefused("refused", row["state"])
        row["state"] = state
        return store_cli.StateSet({"state": state})


def _store(monkeypatch, rows, reason=""):
    """Patch the store edge with a `_Fake` holding `rows`, and return it."""
    fake = _Fake(rows, reason)
    monkeypatch.setattr(run_store, "read_queue", fake.read_queue)
    monkeypatch.setattr(run_store, "upsert_row_detail", fake.upsert_row_detail)
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: fake.upserts.append(row) or True)
    monkeypatch.setattr(store_cli, "set_state", fake.set_state)
    monkeypatch.setattr(run_store, "export_hashes", lambda runs_dir: {("ship", "t1"): "stale"})
    monkeypatch.setattr(
        run_store, "set_export_hash", lambda runs_dir, initiative, task_id, digest: fake.hashes.append((initiative, task_id, digest)) or True
    )
    return fake


def _file(tmp_path, relative, text):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_four_flag_edit_changes_the_store_row_and_the_file_together(tmp_path, monkeypatch):
    store = _store(monkeypatch, [_ticket_row()])
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    body_file = _file(tmp_path, "body.txt", "New body\n")
    result = route_edit.apply_edit(
        tmp_path, tmp_path, "t1", title="New title", body_file=str(body_file), surfaces="a.py, b/c.py", state="ready", live_runs=()
    )
    assert result.code == route_edit.DONE
    assert store.states == [("ship", "t1", "ready", route_edit.EDITED_BY, None)]
    assert store.upserts == [
        {"initiative": "ship", "task_id": "t1", "title": "New title", "body": "\nNew body\n", "surfaces": ["a.py", "b/c.py"]}
    ]
    [row] = store.rows
    untouched = ("kind", "initiative", "task_id", "phase", "needs", "extra", "holder", "epoch", "expires_at")
    assert {key: row[key] for key in untouched} == {key: _ticket_row()[key] for key in untouched}
    assert path.read_text(encoding="utf-8") == (
        "---\nstate: ready\ntitle: New title\nneeds:\n- other\nsurfaces:\n- a.py\n- b/c.py\nid: t1\nphase: p1\nrepo: r\n---\n\nNew body\n"
    )
    assert "+state: ready" in result.text and "+title: New title" in result.text
    assert [(initiative, task_id) for initiative, task_id, _ in store.hashes] == [("ship", "t1")]


def test_a_hand_edited_file_is_overwritten_by_the_store_text(tmp_path, monkeypatch):
    store = _store(monkeypatch, [_ticket_row()])
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE.replace("Old body", "Hand edit"))
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", state="ready", live_runs=())
    assert result.code == route_edit.DONE and "-Hand edit" in result.text
    assert path.read_text(encoding="utf-8") == TICKET_FILE.replace("state: todo", "state: ready")
    assert [state for _, _, state, _, _ in store.states] == ["ready"] and store.upserts == [] and len(store.hashes) == 1


def test_an_invalid_state_changes_nothing(tmp_path, monkeypatch):
    store = _store(monkeypatch, [_ticket_row()])
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", title="New title", state="finished", live_runs=())
    assert result.code == route_edit.REFUSED
    assert "invalid state 'finished'" in result.text and "ready" in result.text
    assert path.read_text(encoding="utf-8") == TICKET_FILE
    assert store.upserts == [] and store.states == [] and store.hashes == []


def test_a_store_that_refuses_the_row_leaves_the_file_alone(tmp_path, monkeypatch):
    store = _store(monkeypatch, [_ticket_row()], reason="exit 3")
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", title="New title", live_runs=())
    assert result.code == route_edit.FAILED and "exit 3" in result.text
    assert path.read_text(encoding="utf-8") == TICKET_FILE and store.hashes == []


def test_dry_run_on_a_task_ticket_writes_nothing(tmp_path, monkeypatch):
    store = _store(monkeypatch, [_ticket_row()])
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", state="ready", dry_run=True, live_runs=())
    assert result.code == route_edit.DONE and "+state: ready" in result.text
    assert path.read_text(encoding="utf-8") == TICKET_FILE and store.upserts == [] and store.states == [] and store.hashes == []


def test_surfaces_and_state_are_for_a_task_ticket_only(tmp_path, monkeypatch):
    store = _store(monkeypatch, [_intake_row()])
    path = _file(tmp_path, "intake/fix-it.md", INTAKE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "fix-it", state="ready", live_runs=())
    assert result.code == route_edit.REFUSED and "task ticket only" in result.text
    assert path.read_text(encoding="utf-8") == INTAKE and store.upserts == []


def test_an_unknown_id_keeps_its_error(tmp_path, monkeypatch):
    _store(monkeypatch, [_ticket_row()])
    result = route_edit.apply_edit(tmp_path, tmp_path, "nope", title="New", live_runs=())
    assert (result.code, result.text) == (route_edit.REFUSED, "no queued intake or initiative named 'nope'")


def test_a_task_id_in_two_initiatives_is_refused(tmp_path, monkeypatch):
    store = _store(monkeypatch, [_ticket_row(), {**_ticket_row(), "initiative": "other"}])
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", title="New", live_runs=())
    assert result.code == route_edit.REFUSED and "several initiatives" in result.text and store.upserts == []


def test_an_intake_edit_still_rewrites_the_file_and_the_row(tmp_path, monkeypatch):
    store = _store(monkeypatch, [_intake_row()])
    path = _file(tmp_path, "intake/fix-it.md", INTAKE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "fix-it", title="New title", live_runs=())
    assert result.code == route_edit.DONE
    assert path.read_text(encoding="utf-8") == INTAKE.replace("title: Old title", "title: New title")
    assert [row["title"] for row in store.upserts] == ["New title"]


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


@pytest.mark.parametrize(("current", "new"), [("approved", "ready"), ("in_progress", "ready"), ("done", "ready"), ("done", "approved"), ("dropped", "ready")])
def test_a_backward_state_move_is_refused_and_writes_nothing(tmp_path, monkeypatch, current, new):
    store = _store(monkeypatch, [{**_ticket_row(), "state": current}])
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", state=new, title="New title", live_runs=())
    assert result.code == route_edit.REFUSED and repr(current) in result.text
    assert store.states == [] and store.upserts == [] and store.hashes == []
    assert path.read_text(encoding="utf-8") == TICKET_FILE and store.rows[0]["state"] == current


def test_a_forward_move_goes_through_set_state_with_a_compare_and_set(tmp_path, monkeypatch):
    store = _store(monkeypatch, [{**_ticket_row(), "state": "ready"}])
    _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE.replace("state: todo", "state: ready"))
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", state="approved", live_runs=())
    assert result.code == route_edit.DONE
    assert store.states == [("ship", "t1", "approved", route_edit.EDITED_BY, "ready")] and store.upserts == []


def test_a_state_the_loop_moved_since_the_read_refuses_the_move(tmp_path, monkeypatch):
    store = _store(monkeypatch, [{**_ticket_row(), "state": "ready"}])
    store.on_read = lambda fake: fake.rows[0].update(state="approved")
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", state="dropped", title="New title", live_runs=())
    assert result.code == route_edit.REFUSED and "'approved'" in result.text
    assert store.rows[0]["state"] == "approved" and store.upserts == [] and store.hashes == []
    assert path.read_text(encoding="utf-8") == TICKET_FILE


def test_a_content_edit_leaves_a_concurrently_changed_state_alone(tmp_path, monkeypatch):
    store = _store(monkeypatch, [_ticket_row()])
    store.on_read = lambda fake: fake.rows[0].update(state="in_progress")
    path = _file(tmp_path, "work/ship/p1/t1.md", TICKET_FILE)
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", title="New title", surfaces="a.py", live_runs=())
    assert result.code == route_edit.DONE
    assert store.upserts == [{"initiative": "ship", "task_id": "t1", "title": "New title", "surfaces": ["a.py"]}]
    assert store.states == [] and store.rows[0]["state"] == "in_progress"
    assert "state: in_progress" in path.read_text(encoding="utf-8")


def test_a_state_move_on_a_claimed_row_is_refused(tmp_path, monkeypatch):
    claimed = {**_ticket_row(), "holder": "chair-mac", "expires_at": "2026-10-12T12:00:00+00:00"}
    store = _store(monkeypatch, [claimed])
    result = route_edit.apply_edit(tmp_path, tmp_path, "t1", state="ready", live_runs=(), now="2026-10-12T11:00:00+00:00")
    assert result.code == route_edit.REFUSED and "chair-mac" in result.text and store.states == []


@pytest.mark.parametrize(
    ("current", "new", "refused"),
    [
        ("todo", "ready", False),
        ("ready", "approved", False),
        ("in_progress", "done", False),
        ("quarantined", "ready", False),
        ("ready", "ready", False),
        ("approved", "ready", True),
        ("done", "dropped", True),
        ("dropped", "ready", True),
        ("ready", "in_progress", True),
    ],
)
def test_check_state_move(current, new, refused):
    assert (route_edit.check_state_move(current, new) is not None) is refused


def test_check_claim_lets_an_expired_claim_go():
    row = {**_ticket_row(), "holder": "chair-mac", "expires_at": "2026-10-12T10:00:00+00:00"}
    assert route_edit.check_claim(row, "2026-10-12T11:00:00+00:00") is None
    assert route_edit.check_claim(row, None) is not None
    assert route_edit.check_claim(_ticket_row(), None) is None
