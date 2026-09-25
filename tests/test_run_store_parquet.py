"""`call_events` reads a run's Parquet trace first, then the zst day files, then the loose file."""

import json
import subprocess
import sys
import types

import pytest

from agent_tools import run_store
from agent_tools.store_url import TracesRoot


def row(call, seq, event, run="r1"):
    return {"run_id": run, "call_id": call, "seq": seq, "day": "2026-09-25", "type": "t", "subtype": None, "tool": None,
            "event": event if isinstance(event, str) else json.dumps(event)}


def write_parquet(runs_dir, rows, run="r1", day="2026/09/25"):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = runs_dir / "traces" / day / f"{run}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def stand_in_zst(runs_dir, monkeypatch, rows, run="r1", day="2026/09/25"):
    """A zst day file the stand-in decompressor passes through as plain lines, so this runs without zstandard."""
    stand_in = types.SimpleNamespace(
        ZstdDecompressor=lambda: types.SimpleNamespace(stream_reader=lambda fh, read_across_frames: fh)
    )
    monkeypatch.setitem(sys.modules, "zstandard", stand_in)
    path = runs_dir / "traces" / day / f"{run}.jsonl.zst"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"run_id": run, **r}) + "\n" for r in rows))


@pytest.fixture(autouse=True)
def _no_routing_profile(tmp_path, monkeypatch):
    """A machine's own routing profile must not point these tests at its traces."""
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(tmp_path / "no-such-profile.yaml"))


def profile_with(tmp_path, monkeypatch, provider_lines):
    provider = tmp_path / "provider.yaml"
    provider.write_text(provider_lines)
    routing = tmp_path / "routing.yaml"
    routing.write_text(f"provider_profile: {provider}\n")
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(routing))


def test_the_pure_part_filters_by_call_id_orders_by_seq_and_decodes_the_event_text():
    rows = [row("c1", 2, {"n": "c"}), row("c2", 0, {"n": "x"}), row("c1", 0, {"n": "a"}), row("c1", 1, {"n": "b"})]
    assert run_store._parquet_call_events(rows, "c1") == [{"n": "a"}, {"n": "b"}, {"n": "c"}]
    assert run_store._parquet_call_events(rows, "none") == []


def test_the_pure_part_drops_an_event_that_is_not_a_json_object():
    rows = [row("c1", 0, "not json"), row("c1", 1, "[1]"), row("c1", 2, {"n": "kept"}), {"call_id": "c1", "seq": 3, "event": None}]
    assert run_store._parquet_call_events(rows, "c1") == [{"n": "kept"}]


def test_call_events_reads_the_parquet_file_filtered_by_call_and_ordered_by_seq(tmp_path):
    write_parquet(tmp_path, [row("c1", 1, {"n": "b"}), row("c2", 0, {"n": "x"}), row("c1", 0, {"n": "a"})])
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "a"}, {"n": "b"}]
    assert run_store.call_events(tmp_path, "r1", {"id": "c2"}) == [{"n": "x"}]


def test_a_parquet_file_wins_over_a_zst_file_and_a_loose_file(tmp_path, monkeypatch):
    write_parquet(tmp_path, [row("c1", 0, {"n": "parquet"})])
    stand_in_zst(tmp_path, monkeypatch, [{"call_id": "c1", "seq": 0, "event": {"n": "zst"}}])
    loose = tmp_path / "loose.jsonl"
    loose.write_text('{"n":"loose"}\n')
    assert run_store.call_events(tmp_path, "r1", {"id": "c1", "trace": str(loose)}) == [{"n": "parquet"}]


def test_a_parquet_file_with_no_rows_for_the_call_falls_through_to_zst(tmp_path, monkeypatch):
    write_parquet(tmp_path, [row("c2", 0, {"n": "other"})])
    stand_in_zst(tmp_path, monkeypatch, [{"call_id": "c1", "seq": 0, "event": {"n": "zst"}}])
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "zst"}]


def test_synthetic_call_id_is_the_run_id_and_the_trace_stem_and_none_without_a_trace():
    assert run_store.synthetic_call_id("r1", "/x/r1-trace/build-2.jsonl") == "r1-build-2"
    assert run_store.synthetic_call_id("r1", None) is None
    assert run_store.synthetic_call_id("r1", "") is None


def test_a_call_with_no_id_is_found_under_the_synthetic_id_of_its_trace(tmp_path):
    write_parquet(tmp_path, [row("r1-build-2", 1, {"n": "b"}), row("r1-build-2", 0, {"n": "a"}), row("c2", 0, {"n": "x"})])
    assert run_store.call_events(tmp_path, "r1", {"trace": "/x/r1-trace/build-2.jsonl"}) == [{"n": "a"}, {"n": "b"}]


def test_a_call_with_no_id_is_found_under_the_store_id_for_its_trace_path(tmp_path):
    """After graphs #445 relinks the traces, a pre-id call's Parquet rows carry the store's `legacy:` id."""
    import sqlite3

    trace = "/x/r1-trace/build-2.jsonl"
    conn = sqlite3.connect(tmp_path / "cox.db")
    conn.execute("CREATE TABLE node_calls (call_id TEXT PRIMARY KEY, run_id TEXT, detail_json TEXT)")
    conn.execute("INSERT INTO node_calls VALUES (?, ?, ?)", ("legacy:r1:3", "r1", json.dumps({"trace": trace})))
    conn.commit()
    conn.close()
    write_parquet(tmp_path, [row("legacy:r1:3", 0, {"n": "relinked"})])
    assert run_store.call_events(tmp_path, "r1", {"trace": trace}) == [{"n": "relinked"}]


def test_a_call_whose_own_id_matches_wins_over_the_synthetic_id(tmp_path):
    write_parquet(tmp_path, [row("c1", 0, {"n": "own"}), row("r1-build-2", 0, {"n": "synthetic"})])
    assert run_store.call_events(tmp_path, "r1", {"id": "c1", "trace": "/x/r1-trace/build-2.jsonl"}) == [{"n": "own"}]


def test_a_synthetic_id_with_no_rows_leaves_the_fall_through_to_zst(tmp_path, monkeypatch):
    write_parquet(tmp_path, [row("c2", 0, {"n": "other"})])
    stand_in_zst(tmp_path, monkeypatch, [{"call_id": "c1", "seq": 0, "event": {"n": "zst"}}])
    assert run_store.call_events(tmp_path, "r1", {"id": "c1", "trace": "/x/r1-trace/build-2.jsonl"}) == [{"n": "zst"}]


def test_with_no_parquet_file_call_events_falls_through_to_the_zst_file(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    stand_in_zst(tmp_path, monkeypatch, [{"call_id": "c1", "seq": 1, "event": {"n": "b"}}, {"call_id": "c1", "seq": 0, "event": {"n": "a"}}])
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "a"}, {"n": "b"}]


def test_with_no_parquet_and_no_zst_events_call_events_falls_through_to_the_loose_file(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    stand_in_zst(tmp_path, monkeypatch, [{"call_id": "other", "seq": 0, "event": {"n": "x"}}])
    loose = tmp_path / "loose.jsonl"
    loose.write_text('{"n":"loose"}\nnot json\n')
    assert run_store.call_events(tmp_path, "r1", {"id": "c1", "trace": str(loose)}) == [{"n": "loose"}]


def test_with_no_parquet_file_pyarrow_is_never_needed_and_nothing_found_is_none(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) is None


def test_a_parquet_file_without_pyarrow_raises_naming_the_parquet_extra(tmp_path, monkeypatch):
    path = tmp_path / "traces" / "2026" / "09" / "25" / "r1.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    with pytest.raises(run_store.TracesUnavailable, match=r"coxswain-tools\[parquet\]"):
        run_store.call_events(tmp_path, "r1", {"id": "c1"})


def test_an_s3_root_without_pyarrow_cannot_be_probed_and_raises_the_same_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    with pytest.raises(run_store.TracesUnavailable, match=r"coxswain-tools\[parquet\]"):
        run_store._parquet_rows(TracesRoot("s3://bucket/traces", True), "r1")


def test_parquet_readable_reports_pyarrow_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    assert run_store.parquet_readable(TracesRoot(str(tmp_path), False), None) == run_store.ParquetCheck(False, "pyarrow missing")


def test_parquet_readable_is_ok_for_a_local_directory_and_unreachable_for_a_missing_one(tmp_path):
    pytest.importorskip("pyarrow.fs")
    assert run_store.parquet_readable(TracesRoot(str(tmp_path), False), None) == run_store.ParquetCheck(True, "ok")
    assert run_store.parquet_readable(TracesRoot(str(tmp_path / "gone"), False), None) == run_store.ParquetCheck(False, "root unreachable")


def test_a_provider_profile_traces_url_outside_the_runs_dir_is_where_parquet_events_are_read(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    write_parquet(elsewhere, [row("c1", 0, {"n": "profile"})])
    profile_with(tmp_path, monkeypatch, f"traces_url: {elsewhere / 'traces'}\n")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    assert run_store.call_events(runs_dir, "r1", {"id": "c1"}) == [{"n": "profile"}]


def test_with_no_traces_url_the_runs_dir_traces_directory_is_read(tmp_path, monkeypatch):
    write_parquet(tmp_path, [row("c1", 0, {"n": "local"})])
    profile_with(tmp_path, monkeypatch, "storage_url: sqlite:///unused.db\n")
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "local"}]


def test_two_calls_of_one_run_read_the_parquet_file_once(tmp_path, monkeypatch):
    pq = pytest.importorskip("pyarrow.parquet")
    write_parquet(tmp_path, [row("c1", 0, {"n": "a"}), row("c2", 0, {"n": "b"})])
    reads = []
    real = pq.read_table
    monkeypatch.setattr(pq, "read_table", lambda *a, **k: reads.append(a) or real(*a, **k))
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "a"}]
    assert run_store.call_events(tmp_path, "r1", {"id": "c2"}) == [{"n": "b"}]
    assert len(reads) == 1


def test_a_run_with_no_parquet_file_is_found_once_the_file_appears(tmp_path):
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) is None
    write_parquet(tmp_path, [row("c1", 0, {"n": "late"})])
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "late"}]


def hide_pyarrow(monkeypatch):
    def missing():
        raise run_store.TracesUnavailable(run_store._NEEDS_PYARROW)

    monkeypatch.setattr(run_store, "_import_pyarrow", missing)


def with_harness(tmp_path, monkeypatch):
    """A routing profile naming a harness_dir whose `.venv/bin/python` exists; returns that python."""
    python = tmp_path / "harness" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    routing = tmp_path / "routing.yaml"
    routing.write_text(f"harness_dir: {tmp_path / 'harness'}\n")
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(routing))
    return python


def dump_returns(monkeypatch, code, stdout="", stderr=""):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    monkeypatch.setattr(run_store.subprocess, "run", run)
    return calls


def local_root_with_file(tmp_path):
    path = tmp_path / "traces" / "2026" / "09" / "25" / "r1.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    return TracesRoot(str(tmp_path / "traces"), False)


def two_dump_lines():
    return "\n".join(json.dumps(row("c1", i, {"n": i})) for i in range(2)) + "\n"


def test_the_dump_output_is_cut_to_the_parquet_columns():
    assert run_store._dump_rows(two_dump_lines() + "\n") == [
        {"call_id": "c1", "seq": 0, "event": '{"n": 0}'}, {"call_id": "c1", "seq": 1, "event": '{"n": 1}'}]


def test_without_pyarrow_exit_0_gives_the_dumped_rows(tmp_path, monkeypatch):
    python = with_harness(tmp_path, monkeypatch)
    hide_pyarrow(monkeypatch)
    root = local_root_with_file(tmp_path)
    calls = dump_returns(monkeypatch, 0, two_dump_lines())
    assert len(run_store._parquet_rows(root, "r1")) == 2
    assert calls[0][0] == [str(python), "-m", "harness.store_traces", "dump", root.url, "r1"]
    assert calls[0][1] == {"capture_output": True, "text": True, "timeout": 60}


def test_without_pyarrow_call_events_reads_through_the_harness(tmp_path, monkeypatch):
    with_harness(tmp_path, monkeypatch)
    hide_pyarrow(monkeypatch)
    local_root_with_file(tmp_path)
    dump_returns(monkeypatch, 0, json.dumps(row("c1", 0, {"n": "a"})) + "\n")
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "a"}]


def test_without_pyarrow_exit_3_is_no_file(tmp_path, monkeypatch):
    with_harness(tmp_path, monkeypatch)
    hide_pyarrow(monkeypatch)
    dump_returns(monkeypatch, 3)
    assert run_store._parquet_rows(local_root_with_file(tmp_path), "r1") is None


def test_without_pyarrow_exit_2_raises_naming_both_failures(tmp_path, monkeypatch):
    with_harness(tmp_path, monkeypatch)
    hide_pyarrow(monkeypatch)
    dump_returns(monkeypatch, 2, stderr="Traceback\nModuleNotFoundError: No module named 'pyarrow'\n")
    with pytest.raises(run_store.TracesUnavailable) as err:
        run_store._parquet_rows(local_root_with_file(tmp_path), "r1")
    assert str(err.value) == (f"{run_store._NEEDS_PYARROW} (the harness could not read it either): "
                              "exit 2: ModuleNotFoundError: No module named 'pyarrow'")


def test_a_failing_harness_is_run_once_per_run_not_once_per_call(tmp_path, monkeypatch):
    with_harness(tmp_path, monkeypatch)
    hide_pyarrow(monkeypatch)
    local_root_with_file(tmp_path)
    calls = dump_returns(monkeypatch, 2)
    for call_id in ("c1", "c2"):
        with pytest.raises(run_store.TracesUnavailable, match="exit 2"):
            run_store.call_events(tmp_path, "r1", {"id": call_id})
    assert len(calls) == 1


def test_exit_3_is_asked_again_so_a_late_file_is_found(tmp_path, monkeypatch):
    with_harness(tmp_path, monkeypatch)
    hide_pyarrow(monkeypatch)
    root = TracesRoot("s3://bucket/traces", True)
    calls = dump_returns(monkeypatch, 3)
    assert run_store._parquet_rows(root, "r1") is None
    assert run_store._parquet_rows(root, "r1") is None
    assert len(calls) == 2


def test_without_pyarrow_a_harness_timeout_raises(tmp_path, monkeypatch):
    with_harness(tmp_path, monkeypatch)
    hide_pyarrow(monkeypatch)

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(run_store.subprocess, "run", slow)
    with pytest.raises(run_store.TracesUnavailable, match="could not read it either"):
        run_store._parquet_rows(local_root_with_file(tmp_path), "r1")


def test_without_pyarrow_and_no_harness_python_it_raises_as_before(tmp_path, monkeypatch):
    hide_pyarrow(monkeypatch)
    calls = dump_returns(monkeypatch, 0)
    with pytest.raises(run_store.TracesUnavailable) as err:
        run_store._parquet_rows(local_root_with_file(tmp_path), "r1")
    assert str(err.value) == run_store._NEEDS_PYARROW
    assert calls == []


def test_a_harness_dir_with_no_python_file_is_no_harness_python(tmp_path):
    routing = tmp_path / "routing.yaml"
    routing.write_text(f"harness_dir: {tmp_path / 'empty'}\n")
    assert run_store._harness_python_for(str(routing)) is None


def test_with_no_parquet_file_the_harness_is_never_run(tmp_path, monkeypatch):
    with_harness(tmp_path, monkeypatch)
    hide_pyarrow(monkeypatch)
    calls = dump_returns(monkeypatch, 0)
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) is None
    assert calls == []


def test_parquet_readable_reports_through_the_harness_when_it_imports_what_a_dump_needs(tmp_path, monkeypatch):
    python = with_harness(tmp_path, monkeypatch)
    hide_pyarrow(monkeypatch)
    calls = dump_returns(monkeypatch, 0)
    check = run_store.parquet_readable(TracesRoot(str(tmp_path), False), python)
    assert check == run_store.ParquetCheck(True, "through the harness")
    assert calls[0][0] == [str(python), "-c", "import pyarrow.parquet, harness.store_traces"]


def test_parquet_readable_is_not_readable_when_the_harness_cannot_import_them(tmp_path, monkeypatch):
    python = with_harness(tmp_path, monkeypatch)
    hide_pyarrow(monkeypatch)
    dump_returns(monkeypatch, 1)
    check = run_store.parquet_readable(TracesRoot(str(tmp_path), False), python)
    assert check == run_store.ParquetCheck(False, "pyarrow missing")


def test_parquet_readable_still_reports_pyarrow_missing_with_no_harness_python(tmp_path, monkeypatch):
    hide_pyarrow(monkeypatch)
    calls = dump_returns(monkeypatch, 0)
    assert run_store.parquet_readable(TracesRoot(str(tmp_path), False), None) == run_store.ParquetCheck(False, "pyarrow missing")
    assert calls == []
