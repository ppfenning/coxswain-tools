"""The export guard counts every file the harness writes as baselined, holds only a real hand edit, reports that once,
and a quarantine in the store reaches the status line whatever its ticket file says (2026-10-13 regression)."""

import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from agent_tools import chair_read_quarantined, cli, route, route_edit, route_sync_gh, run_store, store_fill
from agent_tools.chair_facts import held_file_keys, quarantine_facts
from agent_tools.chair_plan import plan_held_files
from agent_tools.chair_plan_recover import plan_recover
from agent_tools.chair_report import format_status, needs_chair_items
from agent_tools.chair_types import HELD_FILE_CAUSE, QUARANTINED_CAUSE
from agent_tools.queue_export import (
    HELD_REASON,
    Hashes,
    agreeing_blobs,
    blob_hash,
    export_file,
    export_files,
    own_export,
    read_baseline_record,
    read_held_record,
    record_written,
    record_written_paths,
    workspace_relative,
    write_baseline_record,
)
from agent_tools.queue_rows import render_item, row_path

NOW = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)
TASK = {
    "kind": "task",
    "initiative": "epic-a",
    "task_id": "t1",
    "phase": "p1",
    "state": "ready",
    "needs": [],
    "title": "Land by task",
    "surfaces": ["agent_tools/land.py"],
    "body": "Do the thing.\n",
    "extra": {"id": "t1", "phase": "p1", "lint": []},  # a decompose row keeps id and phase in extra, as the file does
}
TASK_PATH = row_path(TASK)
# The text a decompose writes: its own key order and flow lists, and no `lint:` key the store row carries.
DECOMPOSE_TEXT = (
    "---\nid: t1\nphase: p1\nstate: ready\nneeds: []\nsurfaces: [agent_tools/land.py]\ntitle: Land by task\n---\n"
    "Do the thing.\n"
)
INTAKE = {
    "kind": "intake",
    "initiative": "intake",
    "task_id": "2026-10-13-idea",
    "phase": "",
    "state": "queued",
    "needs": [],
    "title": "Idea",
    "surfaces": [],
    "body": "Some idea.\n",
    "extra": {"id": "idea", "repo": "/home/user/repos/tools"},
}
INTAKE_PATH = row_path(INTAKE)


def _hashes() -> Hashes:
    stored: dict = {}
    return Hashes(stored, lambda initiative, task_id, digest: stored.update({(initiative, task_id): digest}) or True)


def _write(workspace: Path, path: str, text: str) -> None:
    (workspace / path).parent.mkdir(parents=True, exist_ok=True)
    (workspace / path).write_text(text)


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)


def _commit(workspace: Path, path: str, text: str) -> None:
    _write(workspace, path, text)
    _git(workspace, "init", "-q")
    _git(workspace, "add", path)
    _git(workspace, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "baseline")


# --- the record every harness writer uses ---


def test_workspace_relative_names_only_work_and_intake_markdown(tmp_path: Path) -> None:
    assert [
        workspace_relative(tmp_path, tmp_path / TASK_PATH),
        workspace_relative(tmp_path, INTAKE_PATH),
        workspace_relative(tmp_path, tmp_path / "runs" / "x.md"),
        workspace_relative(tmp_path, Path("/home/user/elsewhere/work/a/p1/t.md")),
        workspace_relative(tmp_path, tmp_path / "work" / "a" / "notes.txt"),
    ] == [TASK_PATH, INTAKE_PATH, None, None, None]


def test_record_written_paths_records_the_blob_of_each_file_on_disk_and_skips_the_rest(tmp_path: Path) -> None:
    _write(tmp_path, TASK_PATH, DECOMPOSE_TEXT)
    record_written_paths(tmp_path, [tmp_path / TASK_PATH, tmp_path / "work" / "gone.md", tmp_path / "runs" / "x.json"])
    assert read_baseline_record(tmp_path) == {TASK_PATH: blob_hash(DECOMPOSE_TEXT)}


def test_record_written_keeps_what_other_writers_recorded(tmp_path: Path) -> None:
    write_baseline_record(tmp_path, {INTAKE_PATH: "aaaa"})
    record_written(tmp_path, {TASK_PATH: DECOMPOSE_TEXT})
    assert read_baseline_record(tmp_path) == {INTAKE_PATH: "aaaa", TASK_PATH: blob_hash(DECOMPOSE_TEXT)}


# --- decompose / store-fill: a file the store already agrees with, or whose row the fill inserted ---


def test_own_export_takes_an_extra_key_only_the_store_row_carries() -> None:
    export_text = render_item(TASK)
    assert (own_export(TASK_PATH, DECOMPOSE_TEXT, TASK), own_export(TASK_PATH, DECOMPOSE_TEXT)) == (
        export_text, render_item({**TASK, "extra": {"id": "t1", "phase": "p1"}}),
    )


def test_a_file_that_says_what_the_export_says_agrees_and_a_real_edit_does_not() -> None:
    edited = DECOMPOSE_TEXT.replace("Do the thing.", "Do another thing.")
    writes = [(TASK_PATH, render_item(TASK))]
    stored = {TASK_PATH: TASK}
    assert agreeing_blobs(writes, {TASK_PATH: DECOMPOSE_TEXT}, stored) == {TASK_PATH: blob_hash(DECOMPOSE_TEXT)}
    assert agreeing_blobs(writes, {TASK_PATH: edited}, stored) == {}


def test_regression_a_decompose_written_untracked_ticket_takes_the_later_quarantine(tmp_path: Path, caplog) -> None:
    # 2026-10-13: the decompose wrote the ticket and its row together; the file was never committed, so it had no
    # baseline and every export held it. It now agrees with the row, is rewritten, and so takes the later state.
    _write(tmp_path, TASK_PATH, DECOMPOSE_TEXT)
    hashes = _hashes()
    with caplog.at_level(logging.WARNING):
        assert export_files(tmp_path, [TASK], hashes=hashes).touched == [TASK_PATH]
        assert export_files(tmp_path, [{**TASK, "state": "quarantined"}], hashes=hashes).touched == [TASK_PATH]
    assert "state: quarantined" in (tmp_path / TASK_PATH).read_text()
    assert (caplog.records, read_held_record(tmp_path)) == ([], [])


def test_a_ticket_the_fill_inserted_is_baselined_so_a_store_change_rewrites_it(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, TASK_PATH, DECOMPOSE_TEXT)
    monkeypatch.setattr(store_fill, "_read_store", lambda runs_dir: [])
    monkeypatch.setattr(run_store, "upsert_row_detail", lambda runs_dir, row: "")
    store_fill.fill_workspace(tmp_path)
    assert read_baseline_record(tmp_path) == {TASK_PATH: blob_hash(DECOMPOSE_TEXT)}
    moved = {**TASK, "state": "quarantined", "title": "Retitled in the store"}
    assert export_files(tmp_path, [moved], hashes=_hashes()).touched == [TASK_PATH]


def test_inserted_texts_names_only_files_whose_row_the_store_lacks_first_file_per_key() -> None:
    files = [(tuple(TASK_PATH.split("/")), DECOMPOSE_TEXT), (tuple(INTAKE_PATH.split("/")), render_item(INTAKE))]
    assert store_fill.inserted_texts(files, [INTAKE]) == {TASK_PATH: DECOMPOSE_TEXT}
    assert store_fill.inserted_texts([*files, files[0]], []) == {TASK_PATH: DECOMPOSE_TEXT, INTAKE_PATH: render_item(INTAKE)}


# --- route file, route edit, route sync's issue line, the decompose launch's initiative.md ---


def test_route_file_records_the_intake_it_wrote_so_the_export_rewrites_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: True)
    filed = "---\nid: idea\ntitle: Idea\nrepo: /home/user/repos/tools\n---\nSome idea.\n"
    assert route.write_filed_item(tmp_path / "runs", tmp_path / INTAKE_PATH, "intake", (Path(INTAKE_PATH).name,), filed) == 0
    assert read_baseline_record(tmp_path) == {INTAKE_PATH: blob_hash(filed)}
    assert export_files(tmp_path, [{**INTAKE, "title": "Idea, retitled"}], hashes=_hashes()).touched == [INTAKE_PATH]


EPIC_ROW = {
    "kind": "initiative", "initiative": "epic-a", "task_id": "epic-a", "phase": "", "state": "todo", "needs": [],
    "title": "Old", "surfaces": [], "body": "Old why.\n", "extra": {"id": "epic-a", "phases": ["p1"]},
}
EPIC_TEXT = "---\nid: epic-a\ntitle: Edited\nrepo: /home/user/repos/tools\n---\nWhy.\n"


def _edit_initiative(tmp_path: Path, monkeypatch, stored: list[dict], took: bool) -> list[dict]:
    path = tmp_path / "work" / "epic-a" / "initiative.md"
    path.parent.mkdir(parents=True)
    upserts: list[dict] = []
    monkeypatch.setattr(run_store, "read_queue", lambda runs_dir, initiative=None, kind=None: stored)
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: upserts.append(row) or took)
    assert route_edit._rewrite(path, tmp_path / "runs", False, EPIC_TEXT) is None
    assert path.read_text() == EPIC_TEXT
    return upserts


def test_route_edit_writes_the_initiative_row_first_then_records_the_file(tmp_path: Path, monkeypatch) -> None:
    upserts = _edit_initiative(tmp_path, monkeypatch, [EPIC_ROW], True)
    assert upserts == [{
        "initiative": "epic-a", "task_id": "epic-a", "title": "Edited", "body": "Why.\n",
        "extra": {"id": "epic-a", "phases": ["p1"], "repo": "/home/user/repos/tools"},
    }]
    assert read_baseline_record(tmp_path) == {"work/epic-a/initiative.md": blob_hash(EPIC_TEXT)}


def test_route_edit_records_an_initiative_md_the_store_has_no_row_for(tmp_path: Path, monkeypatch) -> None:
    assert _edit_initiative(tmp_path, monkeypatch, [], True) == []
    assert read_baseline_record(tmp_path) == {"work/epic-a/initiative.md": blob_hash(EPIC_TEXT)}


def test_route_edit_leaves_the_file_unrecorded_when_the_store_refuses_the_edit(tmp_path: Path, monkeypatch) -> None:
    _edit_initiative(tmp_path, monkeypatch, [EPIC_ROW], False)
    assert read_baseline_record(tmp_path) == {}


def test_the_mapping_writer_records_an_initiative_md(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: True)
    text = "---\nid: epic-a\ntitle: A\nrepo: /home/user/repos/tools\n---\nWhy.\n"
    assert cli._write_mapping({"work/epic-a/initiative.md": text}, tmp_path) is None
    assert read_baseline_record(tmp_path) == {"work/epic-a/initiative.md": blob_hash(text)}


def _sync_world(tmp_path: Path, monkeypatch, stored: list[dict]) -> list[dict]:
    filed = "---\nid: idea\ntitle: Idea\nrepo: /home/user/repos/tools\n---\nSome idea.\n"
    _write(tmp_path, INTAKE_PATH, filed)
    upserts: list[dict] = []
    monkeypatch.setattr(run_store, "read_queue", lambda runs_dir, initiative=None, kind=None: stored)
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: upserts.append(row) or True)
    return upserts


def test_route_sync_writes_the_issue_into_the_store_row_then_records_the_file(tmp_path: Path, monkeypatch) -> None:
    upserts = _sync_world(tmp_path, monkeypatch, [INTAKE])
    route_sync_gh._writeback(tmp_path, "idea", "1620")
    text = (tmp_path / INTAKE_PATH).read_text()
    assert "issue: 1620" in text
    assert upserts == [{"initiative": "intake", "task_id": "2026-10-13-idea", "extra": {**INTAKE["extra"], "issue": "1620"}}]
    assert read_baseline_record(tmp_path) == {INTAKE_PATH: blob_hash(text)}
    # The store now carries the issue, so the export keeps the line rather than dropping it.
    assert "issue: '1620'" in render_item({**INTAKE, "extra": upserts[0]["extra"]})


def test_route_sync_leaves_the_file_unrecorded_when_the_store_has_no_row_for_it(tmp_path: Path, monkeypatch) -> None:
    upserts = _sync_world(tmp_path, monkeypatch, [])
    route_sync_gh._writeback(tmp_path, "idea", "1620")
    assert (upserts, read_baseline_record(tmp_path)) == ([], {})


# --- HEAD and a real hand edit ---


def test_a_file_matching_head_is_never_edited_whatever_the_record_says(tmp_path: Path, caplog) -> None:
    committed = render_item({**TASK, "title": "As committed"})
    _commit(tmp_path, TASK_PATH, committed)
    write_baseline_record(tmp_path, {TASK_PATH: blob_hash("some other export\n")})
    with caplog.at_level(logging.WARNING):
        assert export_files(tmp_path, [{**TASK, "state": "quarantined"}], hashes=_hashes()).touched == [TASK_PATH]
    assert caplog.records == []


def test_a_real_hand_edit_is_held_listed_once_and_released_by_a_forced_write(tmp_path: Path, caplog) -> None:
    hand = "---\nstate: ready\ntitle: My own words\n---\nI changed this.\n"
    _write(tmp_path, TASK_PATH, hand)
    with caplog.at_level(logging.WARNING):
        assert export_files(tmp_path, [TASK], hashes=_hashes()).touched == []
    assert (tmp_path / TASK_PATH).read_text() == hand
    assert read_held_record(tmp_path) == [
        {"path": TASK_PATH, "initiative": "epic-a", "reason": HELD_REASON, "blob": blob_hash(hand)}
    ]
    assert len(caplog.records) == 1
    export_file(tmp_path, TASK, force=True, hashes=_hashes())
    assert read_held_record(tmp_path) == []


def test_a_board_export_with_nothing_held_clears_the_held_list(tmp_path: Path) -> None:
    _write(tmp_path, TASK_PATH, "hand\n")
    export_files(tmp_path, [TASK], hashes=_hashes())
    (tmp_path / TASK_PATH).unlink()
    export_files(tmp_path, [TASK], hashes=_hashes())
    assert read_held_record(tmp_path) == []


HELD = {"path": TASK_PATH, "initiative": "epic-a", "reason": HELD_REASON, "blob": "b1"}


def test_a_held_file_is_one_needs_chair_naming_its_path() -> None:
    [action] = plan_held_files({"held_files": [HELD]})
    assert {k: action[k] for k in ("kind", "initiative", "cause", "path", "blob")} == {
        "kind": "needs_chair", "initiative": "epic-a", "cause": HELD_FILE_CAUSE, "path": TASK_PATH, "blob": "b1",
    }
    assert needs_chair_items([action]) == [f"needs chair: {TASK_PATH}:held_file"]


def test_a_held_file_already_reported_is_not_reported_again_until_its_text_changes() -> None:
    reported = held_file_keys([{"action_json": {"kind": "needs_chair", "cause": HELD_FILE_CAUSE, "path": TASK_PATH, "blob": "b1"}}])
    assert reported == [f"{TASK_PATH}|b1"]
    assert plan_held_files({"held_files": [HELD], "held_reported": reported}) == []
    assert len(plan_held_files({"held_files": [{**HELD, "blob": "b2"}], "held_reported": reported})) == 1


def test_an_intake_held_file_is_named_by_its_path_on_the_status_line() -> None:
    [action] = plan_held_files({"held_files": [{**HELD, "path": INTAKE_PATH, "initiative": ""}]})
    assert needs_chair_items([action]) == [f"needs chair: {INTAKE_PATH}:held_file"]


def test_the_export_commit_leaves_each_held_file_out() -> None:
    assert cli.export_commit_excludes([HELD, {"path": ""}]) == [f":(exclude){TASK_PATH}"]


def test_the_export_hook_does_not_commit_a_held_file(tmp_path: Path, monkeypatch) -> None:
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "t")):
        _git(tmp_path, *args)
    (tmp_path / "runs").mkdir()
    hand = "---\nstate: ready\ntitle: Mine\n---\nhand\n"

    def export(runs_dir, workspace):
        _write(workspace, INTAKE_PATH, render_item(INTAKE))
        _write(workspace, TASK_PATH, hand)
        export_files(workspace, [INTAKE, TASK], hashes=_hashes())
        return []

    monkeypatch.setattr(cli, "_export_board", export)
    cli._chair_export_hook(tmp_path / "runs", ("t1",))
    committed = subprocess.run(["git", "-C", str(tmp_path), "ls-files"], capture_output=True, text=True).stdout.split()
    assert (committed, (tmp_path / TASK_PATH).read_text()) == ([INTAKE_PATH], hand)


# --- a quarantine in the store reaches the status line, whatever its file says ---

_LIMITS = {"hard_stop": False, "weekly_fraction": 0.4, "hard_stop_fraction": 0.93, "launch_cap": 1, "go_degraded": False,
           "five_hour_fraction": 0.2, "window_start_day": None, "window_source": "meter", "weekly_source": "meter"}


def test_regression_a_store_quarantine_whose_file_is_held_reaches_the_status_line(tmp_path: Path, monkeypatch) -> None:
    hand = "---\nstate: ready\ntitle: Mine\n---\nhand\n"
    _write(tmp_path, TASK_PATH, hand)
    export_files(tmp_path, [{**TASK, "state": "quarantined"}], hashes=_hashes())
    assert "state: ready" in (tmp_path / TASK_PATH).read_text()  # held: the file still says ready
    store = [{"initiative": "epic-a", "task_id": "t1", "phase": "p1", "state": "quarantined"}]
    monkeypatch.setattr(run_store, "work_items", lambda runs_dir, initiative=None: store)
    quarantined = chair_read_quarantined.read_quarantined(tmp_path, "store")
    facts = {
        "limits": _LIMITS, "dispatch": {"max_in_flight": 1, "live_runs": 0}, "approved": [], "initiatives": [],
        "quarantines": quarantine_facts(quarantined, [], [], set(), lambda initiative, task: False),
        "held_files": read_held_record(tmp_path),
    }
    actions = [*plan_recover(facts, NOW), *plan_held_files(facts)]
    line = format_status(facts, actions, [], NOW)
    assert f"needs chair: epic-a:{QUARANTINED_CAUSE}" in line
    assert f"needs chair: {TASK_PATH}:held_file" in line
    assert "needs chair: none" not in line
