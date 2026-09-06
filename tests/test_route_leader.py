import datetime
import os
import socket

from agent_tools import leader
from agent_tools.cli import _leader_heartbeat_minutes, main

_NOW = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
_DEAD_PID = 999999999
_LIVE_RECORD = {"session": "alice", "pid": 4242, "host": "h1", "taken_at": _NOW.isoformat(), "heartbeat_at": _NOW.isoformat()}
_STALE_HEARTBEAT_RECORD = {**_LIVE_RECORD, "heartbeat_at": (_NOW - datetime.timedelta(minutes=99)).isoformat()}


def _profile(tmp_path):
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path}\n")
    return profile


def _fresh_iso():
    """Real wall-clock time, for a CLI test whose code under test calls
    `datetime.datetime.now(UTC)` itself and must see a lock as heartbeat-fresh."""
    return datetime.datetime.now(datetime.UTC).isoformat()


# -- liveness: pure, on literals --------------------------------------------------


def test_liveness_is_none_with_no_record():
    assert leader.liveness(None, False, _NOW) == "none"


def test_liveness_is_stale_when_the_pid_is_dead():
    assert leader.liveness(_LIVE_RECORD, False, _NOW) == "stale"


def test_liveness_is_live_when_the_pid_is_alive_and_the_heartbeat_is_fresh():
    assert leader.liveness(_LIVE_RECORD, True, _NOW) == "live"


def test_liveness_is_stale_when_the_heartbeat_outages_the_policy_minutes():
    assert leader.liveness(_STALE_HEARTBEAT_RECORD, True, _NOW, heartbeat_minutes=10) == "stale"


def test_liveness_is_stale_not_raised_for_an_unparsable_heartbeat_at():
    assert leader.liveness({**_LIVE_RECORD, "heartbeat_at": "not-a-timestamp"}, True, _NOW) == "stale"


# -- take/beat/release: pure, on literals ------------------------------------------


def test_take_refuses_a_live_lock_and_names_the_holder():
    record, reason = leader.take(_LIVE_RECORD, "bob", 99, "h2", _NOW, 10, True)
    assert record is None
    assert "alice" in reason and "4242" in reason and "h1" in reason


def test_take_refuses_a_stale_lock_without_steal():
    record, reason = leader.take(_LIVE_RECORD, "bob", 99, "h2", _NOW, 10, False, steal=False)
    assert record is None
    assert "alice" in reason


def test_take_with_steal_succeeds_against_a_stale_lock():
    record, reason = leader.take(_LIVE_RECORD, "bob", 99, "h2", _NOW, 10, False, steal=True)
    assert reason == ""
    assert record == {"session": "bob", "pid": 99, "host": "h2", "taken_at": _NOW.isoformat(), "heartbeat_at": _NOW.isoformat(), "runs": []}


def test_beat_refreshes_the_heartbeat_and_appends_a_run_for_the_matching_triple():
    record, reason = leader.beat(_LIVE_RECORD, "alice", 4242, "h1", _NOW + datetime.timedelta(minutes=1), run_id="cos-1")
    assert reason == ""
    assert record["heartbeat_at"] == (_NOW + datetime.timedelta(minutes=1)).isoformat()
    assert record["runs"] == ["cos-1"]


def test_beat_errors_for_a_session_that_does_not_hold_it():
    record, reason = leader.beat(_LIVE_RECORD, "bob", 4242, "h1", _NOW)
    assert record is None and "not held by bob" in reason


def test_beat_errors_for_the_right_session_with_the_wrong_pid():
    """The label alone is not proof of identity: a matching session with a pid the
    lock did not record is not the process that took it."""
    record, reason = leader.beat(_LIVE_RECORD, "alice", 99999, "h1", _NOW)
    assert record is None and "not held by alice" in reason


def test_release_clears_the_lock_for_the_matching_triple():
    record, reason = leader.release(_LIVE_RECORD, "alice", 4242, "h1")
    assert record is None and reason == ""


def test_release_refuses_a_session_that_does_not_hold_it():
    record, reason = leader.release(_LIVE_RECORD, "bob", 99, "h2")
    assert record == _LIVE_RECORD and "not held by bob" in reason


def test_leader_heartbeat_minutes_is_the_documented_default():
    """Cartridge policy resolution is another repository's item (out of scope here);
    this always answers the default until that lands."""
    assert _leader_heartbeat_minutes() == leader.DEFAULT_HEARTBEAT_MINUTES == 10


# -- CLI: a tmp runs dir under a tmp profile ---------------------------------------


def test_cli_status_reports_stale_for_a_lock_whose_pid_is_dead(tmp_path, capsys):
    """The heartbeat is fresh by the real clock, so only the dead pid can be making
    this stale — isolating the fact the status wording claims to report."""
    profile = _profile(tmp_path)
    fresh = _fresh_iso()
    leader.write(tmp_path / "runs", {**_LIVE_RECORD, "host": socket.gethostname(), "pid": _DEAD_PID, "taken_at": fresh, "heartbeat_at": fresh})
    rc = main(["route", "leader", "status", "--profile", str(profile)])
    assert rc == 0
    assert "stale" in capsys.readouterr().out


def test_cli_status_treats_a_foreign_hosts_lock_as_live_since_it_cannot_check_the_remote_pid(tmp_path, capsys):
    """A pid this process cannot check locally must not be judged dead just because
    the number happens to look unused on this machine."""
    profile = _profile(tmp_path)
    fresh = _fresh_iso()
    leader.write(tmp_path / "runs", {**_LIVE_RECORD, "host": "some-other-host", "pid": _DEAD_PID, "taken_at": fresh, "heartbeat_at": fresh})
    rc = main(["route", "leader", "status", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "live" in out and "stale" not in out


def test_cli_take_refuses_a_fresh_live_lock(tmp_path, capsys):
    profile = _profile(tmp_path)
    fresh = _fresh_iso()
    leader.write(tmp_path / "runs", {**_LIVE_RECORD, "host": socket.gethostname(), "pid": os.getpid(), "taken_at": fresh, "heartbeat_at": fresh})
    rc = main(["route", "leader", "take", "--profile", str(profile), "--label", "bob"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "alice" in out


def test_cli_take_steal_succeeds_against_a_stale_lock(tmp_path, capsys):
    profile = _profile(tmp_path)
    runs_dir = tmp_path / "runs"
    fresh = _fresh_iso()
    leader.write(runs_dir, {**_LIVE_RECORD, "host": socket.gethostname(), "pid": _DEAD_PID, "taken_at": fresh, "heartbeat_at": fresh})
    rc = main(["route", "leader", "take", "--profile", str(profile), "--label", "bob", "--steal"])
    assert rc == 0
    assert leader.read(runs_dir)["session"] == "bob"


def test_cli_take_then_beat_succeeds_for_the_same_session(tmp_path, capsys):
    """`take` and `beat` both identify the caller by `os.getppid()` — the process that
    invoked `cox`, not the ephemeral `cox` process itself — so two separate CLI
    invocations from the same long-running session are the same holder."""
    profile = _profile(tmp_path)
    runs_dir = tmp_path / "runs"
    assert main(["route", "leader", "take", "--profile", str(profile), "--label", "cos1"]) == 0
    capsys.readouterr()
    assert main(["route", "leader", "beat", "--profile", str(profile), "--label", "cos1", "--run", "run-42"]) == 0
    assert leader.read(runs_dir)["runs"] == ["run-42"]


def test_cli_take_then_release_succeeds_for_the_same_session(tmp_path, capsys):
    profile = _profile(tmp_path)
    runs_dir = tmp_path / "runs"
    assert main(["route", "leader", "take", "--profile", str(profile), "--label", "cos1"]) == 0
    capsys.readouterr()
    assert main(["route", "leader", "release", "--profile", str(profile), "--label", "cos1"]) == 0
    assert leader.read(runs_dir) is None
