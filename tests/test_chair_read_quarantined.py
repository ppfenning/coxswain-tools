from agent_tools import run_store
from agent_tools.chair_read_quarantined import quarantined_rows, read_quarantined


def test_a_quarantined_item_gives_initiative_phase_and_task_from_its_path():
    assert quarantined_rows([("work/a/p1/t1.md", "quarantined")]) == [{"initiative": "a", "phase": "p1", "task": "t1"}]


def test_an_item_in_any_other_state_gives_no_row():
    assert quarantined_rows([("work/a/p1/t1.md", "ready")]) == []


def test_a_done_item_that_was_once_quarantined_gives_no_row():
    assert quarantined_rows([("work/a/p1/t1.md", "done")]) == []


def test_a_path_not_shaped_work_initiative_phase_task_md_gives_no_row():
    assert quarantined_rows([("work/a/t1.md", "quarantined"), ("work/a/p1/t1.txt", "quarantined")]) == []


def _world(tmp_path, monkeypatch, store_rows):
    item = tmp_path / "work" / "a" / "p1" / "t1.md"
    item.parent.mkdir(parents=True)
    item.write_text("---\nstate: quarantined\n---\nbody\n")
    calls = []
    monkeypatch.setattr(run_store, "work_items", lambda runs_dir, initiative=None: calls.append(runs_dir) or store_rows)
    return calls


def test_under_files_mode_the_file_state_holds_and_the_store_is_not_read(tmp_path, monkeypatch):
    calls = _world(tmp_path, monkeypatch, [{"initiative": "a", "task_id": "t1", "state": "done"}])
    assert (read_quarantined(tmp_path, "files"), calls) == ([{"initiative": "a", "phase": "p1", "task": "t1"}], [])


def test_under_store_mode_a_done_store_row_hides_a_file_once_quarantined(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch, [{"initiative": "a", "task_id": "t1", "state": "done"}])
    assert read_quarantined(tmp_path, "store") == []
