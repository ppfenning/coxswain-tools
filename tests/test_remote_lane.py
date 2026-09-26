import json
from pathlib import Path

from agent_tools.remote_lane import (
    fetch_scope,
    fetched_record_path,
    land_needs_fetch,
    parse_remote_record,
    records_already_in_store,
    refuse_taken_run_id,
    remote_record,
    remote_record_path,
)


def test_remote_record_holds_the_host_and_the_launched_at_it_was_given():
    assert remote_record("box-1", "2026-09-25T10:00:00Z") == {
        "host": "box-1",
        "launched_at": "2026-09-25T10:00:00Z",
    }


def test_remote_record_names_the_repo_only_when_given():
    assert remote_record("box2", "t", "/r") == {"host": "box2", "launched_at": "t", "repo": "/r"}
    assert "repo" not in remote_record("box2", "t")


def test_remote_record_path_is_the_run_name_with_a_remote_json_suffix():
    assert remote_record_path(Path("runs"), "r7") == Path("runs/r7.remote.json")


def test_parse_remote_record_returns_none_on_bad_json_or_a_missing_key():
    good = '{"host": "box-1", "launched_at": "2026-09-25T10:00:00Z"}'
    assert parse_remote_record(good) == {"host": "box-1", "launched_at": "2026-09-25T10:00:00Z"}
    assert parse_remote_record("{not json") is None
    assert parse_remote_record('{"host": "box-1"}') is None


def test_parse_remote_record_round_trips_a_record_with_a_repo_and_without_one():
    with_repo, without = remote_record("box2", "t", "/r"), remote_record("box2", "t")
    assert parse_remote_record(json.dumps(with_repo)) == with_repo
    assert parse_remote_record(json.dumps(without)) == without
    assert parse_remote_record('{"host": "b", "launched_at": "t", "repo": 7}') == {"host": "b", "launched_at": "t"}


def test_refuse_taken_run_id_names_the_id_only_when_it_is_taken():
    assert refuse_taken_run_id("r7", {"r6", "r7"}) == "run id r7 is already taken"
    assert refuse_taken_run_id("r8", {"r6", "r7"}) is None


def test_fetched_record_path_sits_beside_the_remote_record():
    assert fetched_record_path(Path("/w/runs"), "r7") == Path("/w/runs/r7.fetched.json")


def test_land_needs_fetch_is_true_only_with_a_record_and_no_fetched_marker():
    assert land_needs_fetch(True, False) is True
    assert land_needs_fetch(True, True) is False
    assert land_needs_fetch(False, False) is False


def test_records_already_in_store_is_true_when_every_expected_id_is_present():
    assert records_already_in_store(["t1", "t2"], ["t1", "t2"]) is True


def test_records_already_in_store_is_false_when_one_expected_id_is_missing():
    assert records_already_in_store(["t1", "t2"], ["t1"]) is False


def test_records_already_in_store_is_false_when_nothing_is_expected():
    assert records_already_in_store([], ["t1"]) is False


def test_records_already_in_store_ignores_store_ids_that_are_not_expected():
    assert records_already_in_store(["t1"], ["t1", "t9"]) is True


def test_fetch_scope_is_branches_when_the_store_is_complete():
    assert fetch_scope(True) == "branches"


def test_fetch_scope_is_records_and_branches_when_the_store_is_incomplete():
    assert fetch_scope(False) == "records-and-branches"
