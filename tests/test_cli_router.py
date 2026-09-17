import argparse
from unittest.mock import MagicMock

import pytest

from agent_tools import cli, router
from agent_tools.stats_schema import connect


def _policy(mode):
    return {"mode": mode, "default_tier": "standard", "min_n": 20, "deviation_floor": 0.70, "challenger_n": 10}


def _insert(conn, table, row):
    cols = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(row.values()))


def test_off_never_invokes_select_tier_and_returns_the_floor(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_router_policy_for", lambda a, role: _policy("off"))
    selected = MagicMock()
    monkeypatch.setattr(router, "select_tier", selected)
    rc = cli.main(["router", "select", "--role", "build", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    selected.assert_not_called()
    assert '"effective_tier": "standard"' in out


def test_shadow_invokes_select_tier_marks_the_output_and_still_returns_the_floor(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_router_policy_for", lambda a, role: _policy("shadow"))
    monkeypatch.setattr(cli, "_router_stats_window_for", lambda a, role: {"landed_rate": 0.5, "n": 30, "start": None, "end": None})
    monkeypatch.setattr(router, "select_tier", lambda role, stats_window, policy: ("deep", "landed_rate_low"))
    rc = cli.main(["router", "select", "--role", "build", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "shadow" in out and "not authoritative" in out
    assert '"effective_tier": "standard"' in out


def test_on_invokes_select_tier_and_returns_its_result(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_router_policy_for", lambda a, role: _policy("on"))
    monkeypatch.setattr(cli, "_router_stats_window_for", lambda a, role: {"landed_rate": 0.5, "n": 30, "start": None, "end": None})
    monkeypatch.setattr(router, "select_tier", lambda role, stats_window, policy: ("deep", "landed_rate_low"))
    rc = cli.main(["router", "select", "--role", "build", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"effective_tier": "deep"' in out
    assert '"reason": "landed_rate_low"' in out


def test_a_missing_profile_exits_non_zero_with_a_one_line_reason(tmp_path, capsys):
    missing = tmp_path / "no-such-profile.yaml"
    rc = cli.main(["router", "select", "--role", "build", "--profile", str(missing)])
    out = capsys.readouterr().out.strip()
    assert rc != 0
    assert len(out.splitlines()) == 1
    assert str(missing) in out


def test_an_unparseable_profile_exits_non_zero_with_a_one_line_reason(tmp_path, capsys):
    unparseable = tmp_path / "profile.yaml"
    unparseable.write_text("  bogus: true\n")
    rc = cli.main(["router", "select", "--role", "build", "--profile", str(unparseable)])
    out = capsys.readouterr().out.strip()
    assert rc != 0
    assert len(out.splitlines()) == 1
    assert str(unparseable) in out


def test_stats_window_from_report_counts_challenger_calls_in_n():
    report = [
        {"role": "build", "model": "m", "challenger": False, "n_calls": 12, "landed_rate": 0.8},
        {"role": "build", "model": "m", "challenger": True, "n_calls": 3, "landed_rate": 1.0},
    ]
    window = cli._stats_window_from_report(report, "build")
    assert window["n"] == 15
    assert window["landed_rate"] == pytest.approx(0.8)


def test_router_policy_for_reads_the_router_flag_and_resolves_the_floor_through_the_same_chain_as_bounds(tmp_path):
    provider = tmp_path / "provider.yaml"
    provider.write_text("tier_overrides:\n  scope_epic: cheap\ndefaults:\n  build: standard\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"router: shadow\nprovider_profile: {provider}\n")
    a = argparse.Namespace(profile=str(profile))

    epic_policy = cli._router_policy_for(a, "scope_epic")
    build_policy = cli._router_policy_for(a, "build")

    assert epic_policy["mode"] == "shadow"
    assert epic_policy["default_tier"] == "cheap"
    assert build_policy["mode"] == "shadow"
    assert build_policy["default_tier"] == "standard"


def test_router_policy_for_defaults_to_off_with_no_router_key_in_the_profile(tmp_path):
    provider = tmp_path / "provider.yaml"
    provider.write_text("defaults:\n  build: standard\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"provider_profile: {provider}\n")
    a = argparse.Namespace(profile=str(profile))

    policy = cli._router_policy_for(a, "build")

    assert policy["mode"] == "off"


def test_router_stats_window_for_counts_a_challenger_row_in_n_but_excludes_it_from_landed_rate(tmp_path):
    db = tmp_path / "stats.db"
    conn = connect(db)
    calls = [
        {"run_id": "r1", "seq": 1, "role": "build", "model": "sonnet", "task_id": "r1:p1:t1",
         "join_confidence": "heuristic", "challenger": 1},
        {"run_id": "r1", "seq": 2, "role": "build", "model": "sonnet", "task_id": "r1:p1:t2",
         "join_confidence": "heuristic"},
        {"run_id": "r1", "seq": 3, "role": "build", "model": "sonnet", "task_id": "r1:p1:t3",
         "join_confidence": "heuristic"},
    ]
    tasks = [
        {"run_id": "r1", "task_id": "r1:p1:t1", "outcome": "landed"},
        {"run_id": "r1", "task_id": "r1:p1:t2", "outcome": "landed"},
        {"run_id": "r1", "task_id": "r1:p1:t3", "outcome": "quarantined"},
    ]
    for row in calls:
        _insert(conn, "calls", row)
    for row in tasks:
        _insert(conn, "tasks", row)
    conn.commit()
    conn.close()
    a = argparse.Namespace(db=str(db))

    window = cli._router_stats_window_for(a, "build")

    assert window["n"] == 3
    assert window["landed_rate"] == pytest.approx(0.5)
