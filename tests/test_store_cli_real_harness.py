"""The wire against graphs' real harness: one lease acquired, renewed and released through `store_cli`."""

import pytest

from agent_tools import run_store, store_cli
from agent_tools.store_cli import LeaseGranted, LeaseReleased
from agent_tools.store_url import default_url

HARNESS_PYTHON = run_store._harness_python()

pytestmark = pytest.mark.skipif(HARNESS_PYTHON is None, reason="the routing profile names no harness python")


def test_a_lease_is_acquired_renewed_and_released_through_the_real_harness(monkeypatch, tmp_path):
    # Overrides the autouse `_no_real_harness` fixture here only, with the store pinned under tmp_path.
    monkeypatch.setattr(store_cli, "_harness_python", lambda: HARNESS_PYTHON)
    monkeypatch.setattr(store_cli, "_store_url", lambda runs_dir: default_url(tmp_path))
    assert store_cli.lease_acquire(tmp_path, "chair", "me", 60) == LeaseGranted(1, "me")
    assert store_cli.lease_renew(tmp_path, "chair", "me", 1, 60) == LeaseGranted(1, "me")
    assert store_cli.lease_release(tmp_path, "chair", "me", 1) == LeaseReleased()
    assert (tmp_path / "cox.db").is_file()
