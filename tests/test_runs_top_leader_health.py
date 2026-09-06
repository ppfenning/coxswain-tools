import datetime
import json
import os
import socket

import pytest

from agent_tools import cli, notify, runs_top


def _leader(state: str = "live", holder: str = "loop-a") -> dict:
    return {"holder": holder, "state": state, "minutes_ago": 12}


def test_a_run_whose_launched_by_differs_from_the_live_leader_is_orphaned():
    r = runs_top.row("r1", True, [], [], [], None, "loop-b", _leader())
    assert r.status == "orphaned"
    assert runs_top.highlight(r) == "alert"


def test_a_run_matching_the_live_leaders_holder_is_not_orphaned():
    r = runs_top.row("r1", True, [], [], [], None, "loop-a", _leader())
    assert r.status == "running"


def test_a_run_is_orphaned_when_the_leader_is_stale():
    r = runs_top.row("r1", True, [], [], [], None, "loop-a", _leader("stale"))
    assert r.status == "orphaned"


def test_a_run_is_orphaned_when_the_leader_is_crashed():
    r = runs_top.row("r1", True, [], [], [], None, "loop-a", _leader("crashed"))
    assert r.status == "orphaned"


def test_no_leader_file_is_not_an_alert():
    r = runs_top.row("r1", True, [], [], [], None, "", None)
    assert r.status == "running"


def test_the_leader_line_and_by_column_render_from_literals():
    rows = [runs_top.row("r1", True, [], [], [], None, "loop-b", _leader("stale"))]
    lines = runs_top.render(rows, 80, _leader("stale"))
    assert lines[0] == "leader: loop-a (stale, beat 12m ago)"
    assert "BY" in lines[1]
    assert lines[2].rstrip().endswith("loop-b")


def test_render_with_no_leader_arg_omits_the_leader_line():
    lines = runs_top.render([], 40)
    assert lines[0].startswith("RUN")


def test_a_stale_leader_line_highlights_alert_a_live_one_does_not():
    assert runs_top.leader_highlight(_leader("stale")) == "alert"
    assert runs_top.leader_highlight(_leader("crashed")) == "alert"
    assert runs_top.leader_highlight(_leader("live")) == "normal"
    assert runs_top.leader_highlight(None) == "normal"


def test_leader_notifications_fires_only_on_a_live_to_stale_or_none_transition_with_a_live_run():
    lost = notify.Notification("loop leader", "loop leader lost its heartbeat", "critical")
    assert notify.leader_notifications("live", "stale", True) == [lost]
    assert notify.leader_notifications("live", "crashed", True) == [lost]
    assert notify.leader_notifications("live", "none", True) == [lost]
    assert notify.leader_notifications("live", "live", True) == []
    assert notify.leader_notifications("live", "stale", False) == []
    assert notify.leader_notifications(None, "stale", True) == []


def test_a_leader_transition_from_live_to_crashed_with_a_live_run_notifies(tmp_path):
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    (tmp_path / "leader.json").write_text(
        json.dumps({"session": "loop-a", "pid": 111, "host": socket.gethostname(), "taken_at": now_iso, "heartbeat_at": now_iso}),
        encoding="utf-8",
    )
    (tmp_path / "r1.log").write_text("n1 verdict: land\n", encoding="utf-8")  # no run_exited line: r1 reads as alive
    calls = []
    alive_flags = iter([True, False])  # the leader's own pid, live then dead

    def fake_pid_alive(pid):
        return next(alive_flags)

    sleeps = {"n": 0}

    def fake_sleep(_interval):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise RuntimeError("stop")

    with pytest.raises(RuntimeError):
        notify.run_loop(tmp_path, send=calls.append, sleep=fake_sleep, pid_alive=fake_pid_alive)

    bodies = [argv[-1] for argv in calls]
    assert "loop leader lost its heartbeat" in bodies


def test_cli_runs_top_once_uses_the_resolved_heartbeat_minutes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_leader_heartbeat_minutes", lambda: 1)
    stale_at = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
    (tmp_path / "leader.json").write_text(
        json.dumps({"session": "loop-a", "pid": os.getpid(), "host": "h", "taken_at": stale_at, "heartbeat_at": stale_at}),
        encoding="utf-8",
    )

    rc = cli.main(["runs", "top", "--runs-dir", str(tmp_path), "--once"])

    assert rc == 0
    assert "leader: loop-a (stale, beat 5m ago)" in capsys.readouterr().out


def test_cli_runs_notify_threads_the_resolved_heartbeat_minutes_into_run_loop(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_leader_heartbeat_minutes", lambda: 7)
    captured = {}

    def fake_run_loop(runs_dir, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli.notify, "run_loop", fake_run_loop)

    rc = cli.main(["runs", "notify", "--runs-dir", str(tmp_path), "--once"])

    assert rc == 0
    assert captured["heartbeat_minutes"] == 7
