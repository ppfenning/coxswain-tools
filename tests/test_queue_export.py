import logging
import subprocess
from pathlib import Path

import pytest

from agent_tools.queue_export import (
    HELD_REASON,
    Hashes,
    Held,
    blob_hash,
    classify_file,
    committed_blob,
    export_files,
    export_rows,
    held_files,
    is_contentless,
    may_overwrite,
    plan_export,
    read_baseline_record,
    read_held,
    skipped_ids,
)
from agent_tools.queue_rows import render_item, row_path

TASK_ROW = {
    "kind": "task",
    "initiative": "queue",
    "task_id": "codec",
    "phase": "q",
    "state": "doing",
    "needs": ["a-1"],
    "title": "Codec: rows",
    "surfaces": ["agent_tools/x.py"],
    "body": "Body line.\n",
    "extra": {},
}
INTAKE_ROW = {
    "kind": "intake",
    "initiative": "intake",
    "task_id": "idea",
    "phase": "",
    "state": "queued",
    "needs": [],
    "title": "Idea",
    "surfaces": [],
    "body": "Some idea.\n",
    "extra": {},
}
DONE_ROW = {**INTAKE_ROW, "state": "done"}
INITIATIVE_ROW = {
    "kind": "initiative",
    "initiative": "queue",
    "task_id": "queue",
    "phase": "",
    "state": "todo",
    "needs": [],
    "title": "Queue",
    "surfaces": [],
    "body": "Why.\n",
    "extra": {
        "id": "queue",
        "intake": "intake/idea.md",
        "phases": [{"id": "q", "goal": "Build it"}],
    },
}
STAMPED_ROW = {**INTAKE_ROW, "state": "done", "extra": {"initiative": "queue"}}
EMPTY_ROW = {
    "kind": None,
    "initiative": None,
    "task_id": "ghost",
    "phase": None,
    "state": None,
    "needs": None,
    "title": None,
    "surfaces": None,
    "body": None,
    "extra": None,
}


def test_export_rows_initiative() -> None:
    assert export_rows([INITIATIVE_ROW]) == {
        "work/queue/initiative.md": (
            "---\ntitle: Queue\nid: queue\nintake: intake/idea.md\nphases:\n- id: q\n  goal: Build it\n---\nWhy.\n"
        )
    }


def test_export_rows_task_with_needs_and_surfaces() -> None:
    assert export_rows([TASK_ROW]) == {
        "work/queue/q/codec.md": (
            "---\nstate: doing\ntitle: 'Codec: rows'\nneeds:\n- a-1\nsurfaces:\n- agent_tools/x.py\n---\nBody line.\n"
        )
    }


def test_export_rows_stamped_intake() -> None:
    assert export_rows([STAMPED_ROW]) == {
        "intake/done/idea.md": "---\ntitle: Idea\ninitiative: queue\n---\nSome idea.\n"
    }


def test_export_rows_is_deterministic() -> None:
    rows = [INITIATIVE_ROW, TASK_ROW, STAMPED_ROW]
    before = [dict(row) for row in rows]
    assert export_rows(rows) == export_rows(rows)
    assert rows == before


def test_plan_export_new_file() -> None:
    writes, deletes = plan_export([TASK_ROW], {})
    assert writes == [(row_path(TASK_ROW), render_item(TASK_ROW))]
    assert deletes == []


def test_plan_export_changed_file() -> None:
    path = row_path(TASK_ROW)
    writes, deletes = plan_export([TASK_ROW], {path: "stale text"})
    assert writes == [(path, render_item(TASK_ROW))]
    assert deletes == []


def test_plan_export_unchanged_is_empty() -> None:
    path = row_path(TASK_ROW)
    writes, deletes = plan_export([TASK_ROW], {path: render_item(TASK_ROW)})
    assert (writes, deletes) == ([], [])


def test_plan_export_moved_intake_writes_and_deletes() -> None:
    old_path = row_path(INTAKE_ROW)
    new_path = row_path(DONE_ROW)
    writes, deletes = plan_export([DONE_ROW], {old_path: render_item(INTAKE_ROW)})
    assert writes == [(new_path, render_item(DONE_ROW))]
    assert deletes == [old_path]


def test_plan_export_initiative_md_untouched() -> None:
    files_on_disk = {"work/queue/initiative.md": "# Queue\n"}
    writes, deletes = plan_export([], files_on_disk)
    assert (writes, deletes) == ([], [])


def test_plan_export_empty_rows_is_empty() -> None:
    assert plan_export([], {}) == ([], [])


def test_export_files_second_export_is_noop(tmp_path: Path) -> None:
    rows = [TASK_ROW, INTAKE_ROW]
    (tmp_path / "work" / "queue").mkdir(parents=True)
    (tmp_path / "work" / "queue" / "initiative.md").write_text("# Queue\n")

    first, skipped = export_files(tmp_path, rows)
    assert skipped == []
    assert first == sorted([row_path(TASK_ROW), row_path(INTAKE_ROW)])
    assert (tmp_path / row_path(TASK_ROW)).read_text() == render_item(TASK_ROW)
    assert (tmp_path / row_path(INTAKE_ROW)).read_text() == render_item(INTAKE_ROW)
    assert (tmp_path / "work" / "queue" / "initiative.md").read_text() == "# Queue\n"

    assert export_files(tmp_path, rows) == ([], [])


def test_export_files_empty_rows_is_noop(tmp_path: Path) -> None:
    (tmp_path / "intake").mkdir()
    (tmp_path / "intake" / "stale.md").write_text("---\ntitle: Stale\n---\n")

    assert export_files(tmp_path, []) == ([], [])
    assert (tmp_path / "intake" / "stale.md").exists()


def test_is_contentless() -> None:
    assert is_contentless(EMPTY_ROW)
    assert is_contentless({**TASK_ROW, "kind": None})
    assert not any(is_contentless(row) for row in (TASK_ROW, INTAKE_ROW, INITIATIVE_ROW))


def test_skipped_ids_names_contentless_rows() -> None:
    assert skipped_ids([TASK_ROW, EMPTY_ROW]) == ["ghost"]
    assert skipped_ids([{**EMPTY_ROW, "task_id": None}, TASK_ROW, {**EMPTY_ROW, "task_id": None}]) == ["#0", "#2"]


def test_export_rows_fills_partly_empty_row() -> None:
    row = {**TASK_ROW, "extra": None, "body": None, "needs": None}
    assert export_rows([row]) == {row_path(TASK_ROW): render_item({**TASK_ROW, "body": "", "needs": []})}


def test_export_files_only_contentless_rows_changes_nothing(tmp_path: Path) -> None:
    (tmp_path / "intake").mkdir()
    (tmp_path / "intake" / "stray.md").write_text("---\ntitle: T\n---\n")
    assert export_files(tmp_path, [EMPTY_ROW]) == ([], ["ghost"])
    assert (tmp_path / "intake" / "stray.md").exists()


def test_export_files_keeps_file_named_by_skipped_row(tmp_path: Path) -> None:
    hollow = {**TASK_ROW, "title": None, "body": None, "extra": None}
    (tmp_path / row_path(TASK_ROW)).parent.mkdir(parents=True)
    (tmp_path / row_path(TASK_ROW)).write_text("last copy\n")
    touched, skipped = export_files(tmp_path, [INTAKE_ROW, hollow])
    assert skipped == ["codec"]
    assert touched == [row_path(INTAKE_ROW)]
    assert (tmp_path / row_path(TASK_ROW)).read_text() == "last copy\n"


def test_export_rows_skips_contentless_row() -> None:
    assert list(export_rows([TASK_ROW, EMPTY_ROW])) == [row_path(TASK_ROW)]


def test_plan_export_spares_held_and_done() -> None:
    files_on_disk = {"intake/held/a.md": "x", "intake/done/b.md": "y", "intake/stray.md": "z"}
    _, deletes = plan_export([TASK_ROW], files_on_disk)
    assert deletes == ["intake/stray.md"]


def test_export_files_skips_contentless_row(tmp_path: Path) -> None:
    touched, skipped = export_files(tmp_path, [TASK_ROW, EMPTY_ROW])
    assert skipped == ["ghost"]
    assert touched == [row_path(TASK_ROW)]
    assert (tmp_path / row_path(TASK_ROW)).read_text() == render_item(TASK_ROW)


def test_export_files_keeps_held_and_done_deletes_stray(tmp_path: Path) -> None:
    for name in ("held/h.md", "done/d.md", "stray.md"):
        path = tmp_path / "intake" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\ntitle: T\n---\n")

    touched, _ = export_files(tmp_path, [TASK_ROW])
    assert (tmp_path / "intake" / "held" / "h.md").exists()
    assert (tmp_path / "intake" / "done" / "d.md").exists()
    assert not (tmp_path / "intake" / "stray.md").exists()
    assert "intake/stray.md" in touched


# The hand-edit guard: a file is compared with this workspace's own baseline, never the store's per-row hash.

GUARD_PATH = row_path(TASK_ROW)


def _hashes(stored: dict | None = None) -> Hashes:
    held = {} if stored is None else stored
    return Hashes(held, lambda initiative, task_id, digest: held.update({(initiative, task_id): digest}) or True)


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)


def _commit(workspace: Path, path: str, text: str) -> None:
    full = workspace / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(text)
    _git(workspace, "init", "-q")
    _git(workspace, "add", path)
    _git(workspace, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "baseline")


@pytest.mark.parametrize(
    ("file_hash", "baselines", "export_hash", "status"),
    [
        (None, {"b"}, "e", "missing"),
        (None, set(), "e", "missing"),
        ("e", {"b"}, "e", "in_sync"),
        ("e", set(), "e", "in_sync"),
        ("b", {"b"}, "e", "behind"),
        ("r", {"b", "r"}, "e", "behind"),
        ("x", {"b"}, "e", "edited"),
        ("x", set(), "e", "edited"),
    ],
)
def test_classify_file_on_literals(file_hash, baselines, export_hash, status) -> None:
    assert classify_file(file_hash, baselines, export_hash) == status


def test_may_overwrite_with_no_baseline_writes_only_a_missing_or_equal_file() -> None:
    assert may_overwrite(None, set(), "new\n") is True
    assert may_overwrite("new\n", set(), "new\n") is True
    assert may_overwrite("old\n", set(), "new\n") is False
    assert may_overwrite("old\n", {blob_hash("old\n")}, "new\n") is True


def test_blob_hash_matches_git(tmp_path: Path) -> None:
    _commit(tmp_path, "work/a.md", "some text\n")
    assert committed_blob(tmp_path, "work/a.md") == blob_hash("some text\n")
    assert committed_blob(tmp_path, "work/untracked.md") is None


def test_committed_blob_outside_git_is_none(tmp_path: Path) -> None:
    assert committed_blob(tmp_path / "nowhere", "work/a.md") is None


def test_regression_another_host_recorded_the_store_hash_and_the_unedited_file_is_rewritten(tmp_path: Path, caplog) -> None:
    older = {**TASK_ROW, "title": "Older body"}
    _commit(tmp_path, GUARD_PATH, render_item(older))
    hashes = _hashes({("queue", "codec"): blob_hash("another machine's newer export\n")})
    with caplog.at_level(logging.WARNING):
        touched, _ = export_files(tmp_path, [TASK_ROW], hashes=hashes)
    assert touched == [GUARD_PATH]
    assert (tmp_path / GUARD_PATH).read_text() == render_item(TASK_ROW)
    assert caplog.records == []


def test_file_behind_its_last_export_but_not_yet_committed_is_rewritten_silently(tmp_path: Path, caplog) -> None:
    _commit(tmp_path, GUARD_PATH, render_item({**TASK_ROW, "title": "v1"}))
    hashes = _hashes()
    export_files(tmp_path, [{**TASK_ROW, "title": "v2"}], hashes=hashes)
    assert read_baseline_record(tmp_path)[GUARD_PATH] == blob_hash(render_item({**TASK_ROW, "title": "v2"}))
    with caplog.at_level(logging.WARNING):
        touched, _ = export_files(tmp_path, [{**TASK_ROW, "title": "v3"}], hashes=hashes)
    assert touched == [GUARD_PATH]
    assert "v3" in (tmp_path / GUARD_PATH).read_text()
    assert caplog.records == []


def test_untracked_file_falls_back_to_the_workspace_record(tmp_path: Path, caplog) -> None:
    hashes = _hashes()
    export_files(tmp_path, [TASK_ROW], hashes=hashes)
    with caplog.at_level(logging.WARNING):
        touched, _ = export_files(tmp_path, [{**TASK_ROW, "title": "Newer"}], hashes=hashes)
    assert touched == [GUARD_PATH]
    assert caplog.records == []


def test_untracked_file_with_no_baseline_is_left_alone_with_a_warning(tmp_path: Path, caplog) -> None:
    (tmp_path / GUARD_PATH).parent.mkdir(parents=True)
    (tmp_path / GUARD_PATH).write_text("hand written\n")
    with caplog.at_level(logging.WARNING):
        touched, _ = export_files(tmp_path, [TASK_ROW], hashes=_hashes())
    assert touched == []
    assert (tmp_path / GUARD_PATH).read_text() == "hand written\n"
    assert len(caplog.records) == 1


def test_edited_committed_file_is_left_alone_and_the_warning_leads_with_a_diff(tmp_path: Path, caplog) -> None:
    _commit(tmp_path, GUARD_PATH, render_item(TASK_ROW))
    (tmp_path / GUARD_PATH).write_text(render_item(TASK_ROW) + "\nhand edit\n")
    with caplog.at_level(logging.WARNING):
        touched, _ = export_files(tmp_path, [{**TASK_ROW, "title": "Changed in the store"}], hashes=_hashes())
    assert touched == []
    assert (tmp_path / GUARD_PATH).read_text().endswith("hand edit\n")
    [record] = caplog.records
    line = record.getMessage()
    assert f"git -C {tmp_path} diff HEAD -- {GUARD_PATH}" in line
    assert line.index("diff HEAD") < line.index("cox route import")
    assert "overwrites the store with the file" in line


def test_missing_file_is_written_and_in_sync_file_untouched(tmp_path: Path) -> None:
    hashes = _hashes()
    assert export_files(tmp_path, [TASK_ROW], hashes=hashes).touched == [GUARD_PATH]
    assert export_files(tmp_path, [TASK_ROW], hashes=hashes).touched == []


# --- held_files: every file the export holds, with its initiative ---

HAND = "hand edit\n"


def test_held_files_returns_every_held_file_with_its_initiative_in_path_order() -> None:
    paths = ["work/beta/p1/c.md", "work/alpha/p2/b.md", "work/alpha/p1/a.md"]
    writes = [(path, "export\n") for path in paths]
    assert held_files(writes, dict.fromkeys(paths, HAND), {}) == [
        Held("work/alpha/p1/a.md", "alpha", HELD_REASON),
        Held("work/alpha/p2/b.md", "alpha", HELD_REASON),
        Held("work/beta/p1/c.md", "beta", HELD_REASON),
    ]


def test_held_files_is_empty_when_every_planned_write_is_behind_its_baseline() -> None:
    writes = [("work/a/p1/x.md", "export\n")]
    assert held_files(writes, {"work/a/p1/x.md": HAND}, {"work/a/p1/x.md": {blob_hash(HAND)}}) == []


def test_a_file_already_equal_to_its_export_is_not_held_even_with_no_baseline() -> None:
    # Not a planned write: the writer heals its hash instead of holding it, so the list must not name it either.
    assert held_files([], {"work/a/p1/x.md": "export\n"}, {}) == []


def test_a_planned_write_with_no_file_on_disk_is_not_held() -> None:
    assert held_files([("work/a/p1/new.md", "export\n")], {}, {}) == []


def test_read_held_names_exactly_the_files_the_export_then_leaves_alone(tmp_path: Path) -> None:
    other = {**TASK_ROW, "initiative": "other", "task_id": "x"}
    for row in (TASK_ROW, other):
        (tmp_path / row_path(row)).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / row_path(row)).write_text(HAND)
    held = read_held(tmp_path, [TASK_ROW, other])
    touched, _ = export_files(tmp_path, [TASK_ROW, other], hashes=_hashes())
    assert ([h.path for h in held], touched) == (sorted([GUARD_PATH, row_path(other)]), [])
