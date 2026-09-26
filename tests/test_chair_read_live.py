from pathlib import Path

from agent_tools.chair_read_live import Lease, live_initiatives, read_live_initiatives

NOW = "2026-09-26T12:00:00Z"
FUTURE = Lease("run-1", "2026-09-26T13:00:00Z")
PAST = Lease("run-1", "2026-09-26T11:00:00Z")


def test_a_held_unexpired_lease_is_live():
    assert live_initiatives({"a": FUTURE}, {"a": False}, NOW) == ["a"]


def test_an_expired_lease_with_a_live_pid_is_live():
    assert live_initiatives({"a": PAST}, {"a": True}, NOW) == ["a"]


def test_an_expired_lease_with_a_dead_pid_is_not_live():
    assert live_initiatives({"a": PAST}, {"a": False}, NOW) == []


def test_a_released_lease_with_no_pid_is_not_live():
    assert live_initiatives({"a": None}, {}, NOW) == []


def test_live_ids_come_back_sorted_across_both_sources():
    assert live_initiatives({"c": FUTURE, "a": PAST}, {"b": True, "a": True}, NOW) == ["a", "b", "c"]


def test_the_edge_reads_no_live_run_from_an_empty_runs_dir(tmp_path: Path):
    assert read_live_initiatives(tmp_path, ["a", "b"], NOW) == []
