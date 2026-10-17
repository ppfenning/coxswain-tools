import os
import sqlite3
from dataclasses import replace

from agent_tools.chair_facts import empty_decompose_facts, gather_facts
from tests.test_chair_facts import NOW, _deps


def test_a_decomposed_intake_with_zero_items_and_an_ended_run_appears():
    facts = empty_decompose_facts(decomposed={"i": "i-decompose-1"}, live=set(), item_counts={"i": 0}, states={})
    assert facts == [{"initiative": "i", "run": "i-decompose-1"}]


def test_a_live_decompose_run_is_not_reported_even_with_zero_items():
    facts = empty_decompose_facts(decomposed={"i": "i-decompose-1"}, live={"i"}, item_counts={"i": 0}, states={})
    assert facts == []


def test_only_the_ended_empty_initiative_is_reported_among_several():
    facts = empty_decompose_facts(
        decomposed={"i": "i-2", "full": "full-1", "busy": "busy-1"},
        live={"busy"},
        item_counts={"i": 0, "full": 3, "busy": 0},
        states={},
    )
    assert facts == [{"initiative": "i", "run": "i-2"}]


def test_one_or_more_stored_items_is_not_reported():
    facts = empty_decompose_facts(decomposed={"i": "i-decompose-1"}, live=set(), item_counts={"i": 2}, states={})
    assert facts == []


def test_an_empty_initiative_in_state_todo_is_reported():
    facts = empty_decompose_facts(
        decomposed={"i": "i-decompose-1"}, live=set(), item_counts={"i": 0}, states={"i": "todo"}
    )
    assert facts == [{"initiative": "i", "run": "i-decompose-1"}]


def test_an_empty_initiative_in_state_done_is_left_out():
    facts = empty_decompose_facts(
        decomposed={"i": "i-decompose-1"}, live=set(), item_counts={"i": 0}, states={"i": "done"}
    )
    assert facts == []


def test_an_empty_initiative_in_state_dropped_is_left_out():
    facts = empty_decompose_facts(
        decomposed={"i": "i-decompose-1"}, live=set(), item_counts={"i": 0}, states={"i": "dropped"}
    )
    assert facts == []


def _gathered(states):
    deps = replace(
        _deps(),
        decomposed_intake=lambda: {"i": "i-decompose-1"},
        item_counts=lambda: {"i": 0},
        initiative_states=lambda: states,
    )
    return gather_facts(deps, NOW)["empty_decompose"]


def test_gather_facts_wires_initiative_states_into_empty_decompose():
    assert _gathered({"i": "done"}) == []
    assert _gathered({"i": "todo"}) == [{"initiative": "i", "run": "i-decompose-1"}]


def test_a_decompose_run_with_an_alive_pid_is_left_out_though_its_row_ended():
    facts = empty_decompose_facts(
        decomposed={"i": "i-decompose-1"}, live=set(), item_counts={"i": 0}, states={}, alive={"i-decompose-1"}
    )
    assert facts == []


def test_a_decompose_run_with_a_dead_pid_is_still_reported():
    facts = empty_decompose_facts(
        decomposed={"i": "i-decompose-1"}, live=set(), item_counts={"i": 0}, states={}, alive=set()
    )
    assert facts == [{"initiative": "i", "run": "i-decompose-1"}]


def _gathered_with_pid(tmp_path, pid):
    conn = sqlite3.connect(tmp_path / "cox.db")
    conn.execute("CREATE TABLE leases (name TEXT, holder TEXT, epoch INTEGER, heartbeat_at TEXT, expires_at TEXT)")
    conn.execute(
        "INSERT INTO leases VALUES ('runs:i-decompose', 'i-decompose-1', 1, '2026-09-25T10:00:00Z', '2026-09-25T10:05:00Z')"
    )
    conn.commit()
    conn.close()
    (tmp_path / "i-decompose-1.pid").write_text(str(pid))
    deps = replace(
        _deps(),
        runs_dir=lambda: str(tmp_path),
        decomposed_intake=lambda: {"i": "i-decompose-1"},
        item_counts=lambda: {"i": 0},
    )
    return gather_facts(deps, NOW)["empty_decompose"]


def test_edge_leaves_out_an_initiative_whose_pid_is_alive_despite_an_expired_lease(tmp_path):
    assert _gathered_with_pid(tmp_path, os.getpid()) == []


def test_edge_reports_an_initiative_whose_pid_is_dead_with_an_expired_lease(tmp_path):
    assert _gathered_with_pid(tmp_path, 99999999) == [{"initiative": "i", "run": "i-decompose-1"}]
