import json

import pytest
from test_cli_launch_on import _fake_edge, _no_local_process, _setup
from test_route_cli import _unmeasured_window

from agent_tools import run_store, usage_window
from agent_tools.cli import _host_checkouts, main


@pytest.fixture(autouse=True)
def _no_ccusage(monkeypatch):
    monkeypatch.setattr(usage_window, "gather", _unmeasured_window)


def _row(name, repos, workspace_dir="/srv/a/workspace"):
    versions = {"workspace_dir": workspace_dir, "repos": repos}
    return {"name": name, "ssh": f"me@{name}", "state": "active", "versions_json": json.dumps(versions)}


def test_lookup_returns_the_recorded_repos_of_the_matching_row():
    rows = [_row("a", ["/srv/a/installs/x"]), _row("b", ["/srv/b/installs/y", "/srv/b/installs/z"])]
    assert _host_checkouts(rows, "b") == ("/srv/b/installs/y", "/srv/b/installs/z")


def test_lookup_returns_nothing_for_a_host_with_no_row():
    assert _host_checkouts([_row("a", ["/srv/a/installs/x"])], "b") == ()


def test_lookup_returns_nothing_for_an_empty_host_list():
    assert _host_checkouts([], "a") == ()


def test_dry_run_plan_shows_the_checkout_the_host_recorded(tmp_path, monkeypatch, capsys):
    ws, remote, argv = _setup(tmp_path)
    rows = [_row("box", ["/srv/a/installs/repo", "/srv/a/installs/harness"], str(remote))]
    monkeypatch.setattr(run_store, "hosts", lambda runs_dir: rows)
    _fake_edge(monkeypatch, [])
    _no_local_process(monkeypatch)
    rc = main([*argv, "--on", "box", "--no-claim", "--dry-run"])
    out = capsys.readouterr().out  # a sync step is a multi-line script, so search the whole output
    assert rc == 0
    assert out.count("dry-run: ssh") == 3
    assert "/srv/a/installs/repo" in out
    assert "/srv/a/installs/harness" in out
