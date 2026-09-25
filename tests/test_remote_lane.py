from pathlib import Path

from agent_tools.remote_lane import (
    land_needs_fetch,
    parse_remote_record,
    refuse_taken_run_id,
    remote_record,
    remote_record_path,
)


def test_remote_record_holds_the_host_and_the_launched_at_it_was_given():
    assert remote_record("box-1", "2026-09-25T10:00:00Z") == {
        "host": "box-1",
        "launched_at": "2026-09-25T10:00:00Z",
    }


def test_remote_record_path_is_the_run_name_with_a_remote_json_suffix():
    assert remote_record_path(Path("runs"), "r7") == Path("runs/r7.remote.json")


def test_parse_remote_record_returns_none_on_bad_json_or_a_missing_key():
    good = '{"host": "box-1", "launched_at": "2026-09-25T10:00:00Z"}'
    assert parse_remote_record(good) == {"host": "box-1", "launched_at": "2026-09-25T10:00:00Z"}
    assert parse_remote_record("{not json") is None
    assert parse_remote_record('{"host": "box-1"}') is None


def test_refuse_taken_run_id_names_the_id_only_when_it_is_taken():
    assert refuse_taken_run_id("r7", {"r6", "r7"}) == "run id r7 is already taken"
    assert refuse_taken_run_id("r8", {"r6", "r7"}) is None


def test_land_needs_fetch_is_true_only_with_a_record_and_no_task_records():
    assert land_needs_fetch(True, False) is True
    assert land_needs_fetch(True, True) is False
    assert land_needs_fetch(False, False) is False
