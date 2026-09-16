import argparse
import json

import pytest

from agent_tools import cli, notify
from agent_tools.events import Event

_EXIT = "epic {run}: 1 phase(s) complete, 0 error(s), {q} task(s) quarantined\n"


@pytest.mark.parametrize("event,expected", [
    (Event("r1", "run_exited", 1, {"quarantined": 3}),
     notify.Notification("r1 exited", "3 quarantined", "normal")),
    (Event("r1", "task_quarantined", 1, {"task": "build", "reason": "tests failed"}),
     notify.Notification("r1 task quarantined", "build: tests failed", "critical")),
    (Event("r1", "budget_stop", 1, {}),
     notify.Notification("r1 budget stop", "spend reached the run's budget_usd", "critical")),
    (Event("r1", "run_exited_cost", 1, {"cost_usd": 1.5}),
     notify.Notification("r1 cost", "$1.50", "low")),
])
def test_each_kind_maps_to_its_notification(event, expected):
    assert notify.notifications([event]) == [expected]


def test_a_cost_below_min_cost_usd_is_dropped():
    e = Event("r1", "run_exited_cost", 1, {"cost_usd": 0.02})
    assert notify.notifications([e], {"kinds": notify.DEFAULT_POLICY["kinds"], "min_cost_usd": 0.5}) == []


def test_kinds_this_module_does_not_name_are_dropped():
    events = [Event("r1", "verdict", 1, {}), Event("r1", "node_started", 2, {}), Event("r1", "run_started", 0, {})]
    assert notify.notifications(events) == []


def test_a_kind_absent_from_policy_kinds_is_dropped():
    e = Event("r1", "run_exited", 1, {"quarantined": 0})
    assert notify.notifications([e], {"kinds": ["budget_stop"], "min_cost_usd": 0.0}) == []


def test_notify_argv_on_one_literal():
    argv = notify.notify_argv(notify.Notification("t", "b", "critical"))
    assert argv == ["notify-send", "-u", "critical", "-a", "cox", "t", "b"]


def test_fold_emits_each_runs_exit_once_across_two_batches():
    notes1, states1 = notify.fold({}, {"r1": ([_EXIT.format(run="r1", q=2)], [], None), "r2": ([], [], None)})
    assert [n.title for n in notes1] == ["r1 exited"]

    # r1's exit line reappears, as if resent; r2 exits for the first time.
    notes2, _ = notify.fold(states1, {"r1": ([_EXIT.format(run="r1", q=2)], [], None),
                                       "r2": ([_EXIT.format(run="r2", q=0)], [], None)})
    assert [n.title for n in notes2] == ["r2 exited"]


def test_run_loop_once_sends_each_notification_and_persists_state(tmp_path):
    (tmp_path / "run1.log").write_text(
        "run1 started\nquarantined task: t1 — bad\n" + _EXIT.format(run="run1", q=1), encoding="utf-8")
    sent = []
    assert notify.run_loop(tmp_path, once=True, send=sent.append) == 0
    assert sent == []
    assert json.loads((tmp_path / ".notify-state.json").read_text())["run1"]["emitted_exit"] is True

    (tmp_path / "run2.log").write_text(_EXIT.format(run="run2", q=0), encoding="utf-8")
    assert notify.run_loop(tmp_path, once=True, send=sent.append) == 0
    assert sorted(argv[5] for argv in sent) == ["run2 exited"]

    sent.clear()
    assert notify.run_loop(tmp_path, once=True, send=sent.append) == 0
    assert sent == []


def test_missing_state_seeds_silently_from_three_finished_runs(tmp_path):
    for name, q in [("run1", 0), ("run2", 1), ("run3", 2)]:
        (tmp_path / f"{name}.log").write_text(_EXIT.format(run=name, q=q), encoding="utf-8")
    sent = []
    assert notify.run_loop(tmp_path, once=True, send=sent.append) == 0
    assert sent == []
    state = json.loads((tmp_path / ".notify-state.json").read_text())
    assert sorted(state) == ["run1", "run2", "run3"]
    assert all(state[run]["emitted_exit"] for run in state)


def test_a_run_that_exits_after_seeding_is_emitted_once(tmp_path):
    (tmp_path / "run1.log").write_text(_EXIT.format(run="run1", q=0), encoding="utf-8")
    (tmp_path / "run2.log").write_text(_EXIT.format(run="run2", q=1), encoding="utf-8")
    sent = []
    assert notify.run_loop(tmp_path, once=True, send=sent.append) == 0
    assert sent == []

    (tmp_path / "run3.log").write_text(_EXIT.format(run="run3", q=0), encoding="utf-8")
    assert notify.run_loop(tmp_path, once=True, send=sent.append) == 0
    assert [argv[5] for argv in sent] == ["run3 exited"]

    sent.clear()
    assert notify.run_loop(tmp_path, once=True, send=sent.append) == 0
    assert sent == []


def test_a_torn_state_file_reseeds_silently_instead_of_replaying(tmp_path, capsys):
    (tmp_path / ".notify-state.json").write_text('{"run1": {"log_lines_', encoding="utf-8")
    for name, q in [("run1", 0), ("run2", 1), ("run3", 2)]:
        (tmp_path / f"{name}.log").write_text(_EXIT.format(run=name, q=q), encoding="utf-8")
    sent = []
    assert notify.run_loop(tmp_path, once=True, send=sent.append) == 0
    assert sent == []
    state = json.loads((tmp_path / ".notify-state.json").read_text())
    assert sorted(state) == ["run1", "run2", "run3"]
    assert "seeding 3 run" in capsys.readouterr().out


def test_replay_emits_the_three_on_first_start(tmp_path):
    for name, q in [("run1", 0), ("run2", 1), ("run3", 2)]:
        (tmp_path / f"{name}.log").write_text(_EXIT.format(run=name, q=q), encoding="utf-8")
    sent = []
    assert notify.run_loop(tmp_path, once=True, send=sent.append, replay=True) == 0
    assert sorted(argv[5] for argv in sent) == ["run1 exited", "run2 exited", "run3 exited"]


def test_seed_state_marks_every_run_seen_with_no_notifications():
    entries = {"r1": ([_EXIT.format(run="r1", q=0)], [], None), "r2": ([_EXIT.format(run="r2", q=1)], [], None)}
    states = notify.seed_state(entries)
    assert states["r1"]["emitted_exit"] is True
    assert states["r2"]["emitted_exit"] is True
    assert states["r1"]["log_lines_seen"] == 1


def test_resolved_notify_policy_reads_the_dropped_file_and_falls_back_to_defaults(tmp_path):
    assert cli._resolved_notify_policy(tmp_path) == notify.DEFAULT_POLICY
    (tmp_path / "policy.notify.json").write_text(json.dumps({"kinds": ["budget_stop"], "min_cost_usd": 2.0}),
                                                   encoding="utf-8")
    assert cli._resolved_notify_policy(tmp_path) == {"kinds": ["budget_stop"], "min_cost_usd": 2.0}


def test_runs_notify_cli_dispatch_reads_the_dropped_policy_file_end_to_end(tmp_path, monkeypatch, capsys):
    # Forces the print-instead fallback so the assertion does not depend on
    # whether the machine running this test happens to have notify-send.
    monkeypatch.setattr(notify.shutil, "which", lambda _: None)
    (tmp_path / "policy.notify.json").write_text(json.dumps({"kinds": ["budget_stop"]}), encoding="utf-8")
    (tmp_path / "run1.log").write_text("run1 started\n", encoding="utf-8")
    a = argparse.Namespace(runs_dir=str(tmp_path), once=True, interval=10, replay=False)
    assert cli._runs_notify(a) == 0  # cold start: seeds .notify-state.json silently
    capsys.readouterr()

    (tmp_path / "run1.log").write_text(
        "run1 started\nquarantined task: t1 — bad\nnode 'build' failed in phase1: error_max_budget_usd\n",
        encoding="utf-8")
    assert cli._runs_notify(a) == 0
    out = capsys.readouterr().out
    assert "budget stop" in out
    assert "task quarantined" not in out
