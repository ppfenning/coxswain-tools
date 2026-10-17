"""`route requeue` core: queue the intake, drop the empty initiative, refuse when a task is still live."""

from agent_tools import route_requeue, run_store

INTAKE = {
    "kind": "intake",
    "initiative": "intake",
    "task_id": "idea",
    "state": "decomposed",
    "extra": {"initiative": "demo"},
}
INITIATIVE = {
    "kind": "initiative",
    "initiative": "demo",
    "task_id": "demo",
    "state": "todo",
    "extra": {"intake": "intake/idea.md"},
}
READY = {"kind": "task", "initiative": "demo", "task_id": "a", "state": "ready"}
DROPPED = {"kind": "task", "initiative": "demo", "task_id": "b", "state": "dropped"}


def _store(monkeypatch, rows):
    writes = []
    monkeypatch.setattr(run_store, "read_queue", lambda runs_dir, initiative=None, kind=None: rows)
    monkeypatch.setattr(run_store, "upsert_row_detail", lambda runs_dir, row: writes.append(row) or "")
    return writes


def _requeue(tmp_path, id, dry_run=False):
    return route_requeue.requeue(tmp_path, tmp_path / "runs", id, dry_run=dry_run)


def test_an_empty_initiative_is_requeued_and_dropped(tmp_path, monkeypatch):
    writes = _store(monkeypatch, [INTAKE, INITIATIVE])
    code, _ = _requeue(tmp_path, "demo")
    assert code == 0
    assert writes == [
        {"initiative": "intake", "task_id": "idea", "state": "queued"},
        {"initiative": "demo", "task_id": "demo", "state": "dropped"},
    ]


def test_an_initiative_with_a_ready_task_is_refused(tmp_path, monkeypatch):
    writes = _store(monkeypatch, [INTAKE, INITIATIVE, READY])
    code, message = _requeue(tmp_path, "demo")
    assert (code, writes) == (2, [])
    assert "a (ready)" in message


def test_an_unknown_id_is_refused(tmp_path, monkeypatch):
    writes = _store(monkeypatch, [INTAKE, INITIATIVE])
    code, message = _requeue(tmp_path, "nope")
    assert (code, writes) == (2, [])
    assert "nope" in message


def test_a_second_requeue_writes_nothing(tmp_path, monkeypatch):
    queued = {**INTAKE, "state": "queued"}
    writes = _store(monkeypatch, [queued, {**INITIATIVE, "state": "dropped"}])
    code, message = _requeue(tmp_path, "idea")
    assert (code, writes) == (0, [])
    assert "already requeued" in message


def test_a_dry_run_writes_nothing(tmp_path, monkeypatch):
    writes = _store(monkeypatch, [INTAKE, INITIATIVE])
    code, message = _requeue(tmp_path, "demo", dry_run=True)
    assert (code, writes) == (0, [])
    assert message.startswith("would requeue")


def test_an_initiative_whose_only_tasks_are_dropped_counts_as_empty(tmp_path, monkeypatch):
    writes = _store(monkeypatch, [INTAKE, INITIATIVE, DROPPED])
    code, _ = _requeue(tmp_path, "demo")
    assert code == 0
    assert [row["state"] for row in writes] == ["queued", "dropped"]


UNLINKED_INTAKE = {"kind": "intake", "initiative": "intake", "task_id": "idea", "state": "queued", "extra": {}}
UNLINKED_INITIATIVE = {"kind": "initiative", "initiative": "demo", "task_id": "demo", "state": "todo"}


def _initiative_md(tmp_path, intake):
    path = tmp_path / "work" / "demo" / "initiative.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nintake: {intake}\n---\n\nbody\n")


def test_initiative_md_names_the_intake_when_the_row_has_no_initiative(tmp_path, monkeypatch):
    writes = _store(monkeypatch, [UNLINKED_INTAKE, UNLINKED_INITIATIVE])
    _initiative_md(tmp_path, "intake/idea.md")
    code, message = _requeue(tmp_path, "demo")
    assert code == 0
    assert writes == [{"initiative": "demo", "task_id": "demo", "state": "dropped"}]
    assert "intake idea queued" in message


def test_a_field_naming_an_unknown_intake_is_refused(tmp_path, monkeypatch):
    writes = _store(monkeypatch, [UNLINKED_INTAKE, UNLINKED_INITIATIVE])
    _initiative_md(tmp_path, "intake/ghost.md")
    code, message = _requeue(tmp_path, "demo")
    assert (code, writes) == (2, [])
    assert "ghost" in message


def test_a_field_naming_a_non_queued_intake_is_refused(tmp_path, monkeypatch):
    writes = _store(monkeypatch, [{**UNLINKED_INTAKE, "state": "decomposed"}, UNLINKED_INITIATIVE])
    _initiative_md(tmp_path, "intake/idea.md")
    code, message = _requeue(tmp_path, "demo")
    assert (code, writes) == (2, [])
    assert "decomposed, not queued" in message


def test_no_row_and_no_field_keeps_the_original_refusal(tmp_path, monkeypatch):
    writes = _store(monkeypatch, [UNLINKED_INTAKE, UNLINKED_INITIATIVE])
    code, message = _requeue(tmp_path, "demo")
    assert (code, writes) == (2, [])
    assert message == "requeue: refused: no intake or initiative named demo, or no intake linked to the initiative"


def test_the_row_link_wins_over_the_field(tmp_path, monkeypatch):
    other = {**UNLINKED_INTAKE, "task_id": "other"}
    writes = _store(monkeypatch, [INTAKE, other, INITIATIVE])
    _initiative_md(tmp_path, "intake/other.md")
    code, _ = _requeue(tmp_path, "demo")
    assert code == 0
    assert writes[0] == {"initiative": "intake", "task_id": "idea", "state": "queued"}
