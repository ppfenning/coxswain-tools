from pathlib import Path

import pytest

from agent_tools import store_switch as sw

FILES_DB = "sqlite:////runs/cox.db"
SHARED_DB = "postgresql://cox:hunter2@db.example/cox"
FILES_TRACES = "/runs/traces"
SHARED_TRACES = "s3://bucket/traces"
DAY = "2026/10/14/run1.parquet"


def test_plan_to_shared_is_store_then_traces():
    steps = sw.plan_switch("to_shared", FILES_DB, SHARED_DB, FILES_TRACES, SHARED_TRACES)
    assert [s.kind for s in steps] == ["copy_store", "copy_traces"]
    assert (steps[0].source, steps[0].target) == (FILES_DB, SHARED_DB)
    assert (steps[1].source, steps[1].target) == (FILES_TRACES, SHARED_TRACES)


def test_plan_to_files_swaps_sides():
    steps = sw.plan_switch("to_files", SHARED_DB, FILES_DB, SHARED_TRACES, FILES_TRACES)
    assert [(s.kind, s.target) for s in steps] == [("copy_store", FILES_DB), ("copy_traces", FILES_TRACES)]
    assert "hunter2" not in steps[0].source_display
    assert steps[0].source == SHARED_DB


def test_same_backend_gives_empty_plan():
    assert sw.plan_switch("to_shared", SHARED_DB, SHARED_DB + "2", SHARED_TRACES, SHARED_TRACES + "2") == []
    assert sw.plan_switch("to_files", FILES_DB, "sqlite:////other.db", FILES_TRACES, "/other") == []


def test_target_equal_to_source_is_rejected():
    with pytest.raises(ValueError, match="same as the source store"):
        sw.plan_switch("to_shared", FILES_DB, FILES_DB, FILES_TRACES, SHARED_TRACES)
    with pytest.raises(ValueError, match="same as the source traces root"):
        sw.plan_switch("to_shared", FILES_DB, SHARED_DB, FILES_TRACES, FILES_TRACES + "/")


def test_unknown_direction_is_rejected():
    with pytest.raises(ValueError, match="to_shared or to_files"):
        sw.plan_switch("sideways", FILES_DB, SHARED_DB, FILES_TRACES, SHARED_TRACES)


def test_verify_counts_passes_and_fails():
    assert sw.verify_counts({"runs": 3, "events": 5}, {"runs": 3, "events": 9}) == []
    assert sw.verify_counts({"runs": 3, "events": 5}, {"runs": 2}) == [
        "events holds 0 rows at the target but 5 at the source.",
        "runs holds 2 rows at the target but 3 at the source.",
    ]


def test_render_plan_never_prints_a_password():
    steps = sw.plan_switch("to_files", "postgresql://u:secret@h/db?password=qsecret", FILES_DB, SHARED_TRACES, FILES_TRACES)
    text = sw.render_plan(steps)
    assert "secret" not in text
    assert "u:***@h/db" in text
    assert sw.render_plan([]).startswith("Nothing to do")


def test_parse_counts_reads_json_and_lines():
    assert sw.parse_counts('copying\n{"runs": 2, "events": 7}\n') == {"runs": 2, "events": 7}
    assert sw.parse_counts("runs: 2\nevents = 7\nnoise\n") == {"runs": 2, "events": 7}


def test_second_run_reports_zero_newly_copied():
    steps = sw.plan_switch("to_shared", FILES_DB, SHARED_DB, FILES_TRACES, SHARED_TRACES)
    seen: set[str] = set()

    def copy_store(source: str, target: str) -> dict[str, int]:
        fresh = {"runs"} - seen
        seen.update(fresh)
        return {"runs": len(fresh)}

    def copy_traces(source: str, target: str) -> dict[str, int]:
        fresh = {DAY} - seen
        seen.update(fresh)
        return {"copied": len(fresh), "skipped": 1 - len(fresh)}

    first = sw.run_plan(steps, copy_store=copy_store, copy_traces=copy_traces)
    second = sw.run_plan(steps, copy_store=copy_store, copy_traces=copy_traces)
    assert [r["copied"] for r in first] == [1, 1]
    assert [r["copied"] for r in second] == [0, 0]
    assert all(r["ok"] for r in first + second)
    assert "hunter2" not in repr(first)


def test_failing_copy_store_stops_before_copy_traces():
    steps = sw.plan_switch("to_shared", FILES_DB, SHARED_DB, FILES_TRACES, SHARED_TRACES)
    called: list[str] = []

    def copy_store(source: str, target: str) -> dict[str, int]:
        raise RuntimeError(f"cannot reach {target}")

    def copy_traces(source: str, target: str) -> dict[str, int]:
        called.append(target)
        return {"copied": 0}

    results = sw.run_plan(steps, copy_store=copy_store, copy_traces=copy_traces)
    assert called == []
    assert [(r["kind"], r["ok"]) for r in results] == [("copy_store", False)]
    assert "hunter2" not in results[0]["error"]
    assert "cox:***@db.example" in results[0]["error"]


def test_unparseable_store_copy_output_fails_before_copy_traces():
    steps = sw.plan_switch("to_shared", FILES_DB, SHARED_DB, FILES_TRACES, SHARED_TRACES)
    called: list[str] = []

    def copy_traces(source: str, target: str) -> dict[str, int]:
        called.append(target)
        return {"copied": 0}

    results = sw.run_plan(
        steps, copy_store=lambda s, t: sw.parse_counts("done, see log\n"), copy_traces=copy_traces
    )
    assert called == []
    assert [(r["kind"], r["ok"]) for r in results] == [("copy_store", False)]
    assert results[0]["error"] == "copy_store reported no per-table counts, so the store copy is unverified."
    assert "hunter2" not in repr(results)


def test_traces_result_without_copied_count_fails():
    steps = sw.plan_switch("to_shared", FILES_DB, SHARED_DB, FILES_TRACES, SHARED_TRACES)
    results = sw.run_plan(steps, copy_store=lambda s, t: {"runs": 1}, copy_traces=lambda s, t: {})
    assert [(r["kind"], r["ok"]) for r in results] == [("copy_store", True), ("copy_traces", False)]


def test_target_on_the_wrong_backend_is_rejected():
    with pytest.raises(ValueError, match="to_shared needs a postgresql:// store"):
        sw.plan_switch("to_shared", FILES_DB, "sqlite:////other.db", FILES_TRACES, SHARED_TRACES)
    with pytest.raises(ValueError, match="to_files needs a files store"):
        sw.plan_switch("to_files", SHARED_DB, SHARED_DB + "2", SHARED_TRACES, FILES_TRACES)


def test_same_backend_with_equal_target_is_rejected():
    with pytest.raises(ValueError, match="same as the source store"):
        sw.plan_switch("to_shared", SHARED_DB, SHARED_DB, SHARED_TRACES, SHARED_TRACES + "2")


def test_default_copier_s3_root_without_object_store_fails_the_step():
    steps = sw.plan_switch("to_shared", FILES_DB, SHARED_DB, FILES_TRACES, SHARED_TRACES)
    results = sw.run_plan(steps, copy_store=lambda s, t: {"runs": 1})
    assert [(r["kind"], r["ok"]) for r in results] == [("copy_store", True), ("copy_traces", False)]
    assert "no object_store block" in results[1]["error"]


def test_layout_matches_lake_traces():
    pytest.importorskip("pyiceberg")
    pytest.importorskip("pyarrow")
    from agent_tools import lake_traces

    assert sw._LAYOUT.pattern == lake_traces._LAYOUT.pattern


def test_arrow_tree_copies_through_a_pyarrow_filesystem(tmp_path: Path):
    fs = pytest.importorskip("pyarrow.fs")
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src, DAY, b"aaaa")
    _write(src, "2026/10/15/notes.txt", b"ignored")
    source, target = sw._ArrowTree(fs.LocalFileSystem(), str(src)), sw._ArrowTree(fs.LocalFileSystem(), str(dst))
    assert source.sizes() == {DAY: 4}
    (dst / DAY).parent.mkdir(parents=True)  # object stores need no directories; a local filesystem does
    with source.open_in(DAY) as stream:
        target.write(DAY, stream)
    assert target.sizes() == {DAY: 4}
    assert (dst / DAY).read_bytes() == b"aaaa"


def _write(root: Path, key: str, data: bytes) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_local_traces_copy_skips_existing_same_size_files(tmp_path: Path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src, DAY, b"aaaa")
    _write(src, "2026/10/15/run2.parquet", b"bbbb")
    _write(src, "2026/10/15/notes.txt", b"ignored")
    _write(dst, DAY, b"zzzz")  # already there with the same size: kept, not overwritten
    copy = sw.traces_copier()
    assert copy(str(src), str(dst)) == {"copied": 1, "skipped": 1}
    assert (dst / DAY).read_bytes() == b"zzzz"
    assert (dst / "2026/10/15/run2.parquet").read_bytes() == b"bbbb"
    assert not (dst / "2026/10/15/notes.txt").exists()
    assert copy(str(src), str(dst)) == {"copied": 0, "skipped": 2}
    assert (src / DAY).read_bytes() == b"aaaa"


def test_local_traces_copy_recopies_a_size_mismatch(tmp_path: Path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src, DAY, b"aaaa")
    _write(dst, DAY, b"aa")
    assert sw.traces_copier()(str(src), str(dst)) == {"copied": 1, "skipped": 0}
    assert (dst / DAY).read_bytes() == b"aaaa"
