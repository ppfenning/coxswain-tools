import subprocess
from pathlib import Path

from agent_tools import store_cli
from agent_tools.store_cli import (
    Failed,
    Landed,
    LeaseError,
    LeaseGranted,
    LeaseRefused,
    NotAvailable,
    NotInStore,
)

PY = "/h/.venv/bin/python"
HEAD = [PY, "-m", "harness.store_cli"]


def test_mark_landed_argv():
    assert store_cli.mark_landed_argv(PY, "r1", "p1", "t1", "http://pr/1", "2026-09-25T00:00:00Z") == [
        *HEAD, "mark-landed", "r1", "p1", "t1", "--pr", "http://pr/1", "--at", "2026-09-25T00:00:00Z"]


def test_lease_acquire_argv():
    assert store_cli.lease_acquire_argv(PY, "chair", "me", 60) == [*HEAD, "lease", "acquire", "chair", "me", "--ttl", "60"]


def test_lease_renew_argv():
    assert store_cli.lease_renew_argv(PY, "chair", "me", 4, 60) == [
        *HEAD, "lease", "renew", "chair", "me", "4", "--ttl", "60"]


def test_lease_release_argv():
    assert store_cli.lease_release_argv(PY, "chair", "me", 4) == [*HEAD, "lease", "release", "chair", "me", "4"]


def test_mark_landed_exit_0_is_the_record():
    assert store_cli.parse_mark_landed(0, '{"task": "t1", "landed": true}') == Landed({"task": "t1", "landed": True})


def test_mark_landed_exit_3_is_not_in_store():
    assert store_cli.parse_mark_landed(3, '{"error": "absent"}') == NotInStore()


def test_mark_landed_other_code_is_a_failure_value():
    assert store_cli.parse_mark_landed(2, "bad args") == Failed(2, "bad args")


def test_lease_exit_0_is_granted():
    assert store_cli.parse_lease(0, '{"ok": true, "epoch": 5, "holder": "me"}') == LeaseGranted(5, "me")


def test_lease_exit_3_is_refused_with_a_null_epoch_and_holder():
    assert store_cli.parse_lease(3, '{"ok": false, "epoch": null, "holder": null}') == LeaseRefused(None, None)


def test_lease_exit_2_is_an_error():
    assert store_cli.parse_lease(2, "unreadable store") == LeaseError("exit 2: unreadable store")


def test_edge_runs_subprocess_and_returns_a_value_without_raising(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 3, stdout='{"ok": false, "epoch": 7, "holder": "other"}', stderr="")

    monkeypatch.setattr(store_cli, "_harness_python", lambda: Path(PY))
    monkeypatch.setattr(store_cli.subprocess, "run", fake_run)
    assert store_cli.lease_acquire("chair", "me", 60) == LeaseRefused(7, "other")
    assert seen == [[*HEAD, "lease", "acquire", "chair", "me", "--ttl", "60"]]


def test_a_missing_harness_is_not_available_and_runs_nothing(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(store_cli, "_harness_python", lambda: None)
    monkeypatch.setattr(store_cli.subprocess, "run", boom)
    assert store_cli.mark_landed("r", "p", "t", "u", "a") == NotAvailable()
    assert store_cli.lease_release("chair", "me", 1) == NotAvailable()
