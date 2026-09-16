import json

import pytest

from agent_tools.cli import build_parser, main
from agent_tools.stats_query import bounds_report, coverage_report, explain_report, roles_report, series_report
from agent_tools.stats_schema import connect


def _insert(conn, table, row):
    cols = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(row.values()))


def _seed(db_path):
    conn = connect(db_path)
    calls = [
        {"run_id": "r1", "seq": 1, "role": "build", "model": "sonnet", "task_id": "r1:p1:t1",
         "join_confidence": "heuristic", "failure_class": None},
        {"run_id": "r1", "seq": 2, "role": "build", "model": "sonnet", "task_id": "r1:p1:t1",
         "join_confidence": "heuristic", "failure_class": "tool_error"},
    ]
    tasks = [
        {"run_id": "r1", "task_id": "r1:p1:t1", "attempt": 2, "outcome": "landed", "cost_usd": 1.5},
    ]
    runs = [
        {"run_id": "r1", "cartridge_sha": "abc", "provider_profile": "default"},
    ]
    for row in calls:
        _insert(conn, "calls", row)
    for row in tasks:
        _insert(conn, "tasks", row)
    for row in runs:
        _insert(conn, "runs", row)
    conn.commit()
    conn.close()
    return calls, tasks, runs


def test_cli_stats_roles_json_matches_the_direct_report(tmp_path, capsys):
    db = tmp_path / "stats.db"
    calls, tasks, runs = _seed(db)
    code = main(["stats", "roles", "--db", str(db), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out == roles_report(calls, tasks, runs)


def test_cli_stats_roles_default_text_is_under_the_cap(tmp_path, capsys):
    db = tmp_path / "stats.db"
    _seed(db)
    code = main(["stats", "roles", "--db", str(db)])
    out = capsys.readouterr().out
    assert code == 0
    assert len(out) // 4 <= 300
    assert "role=build" in out


def test_cli_stats_explain_json_matches_the_direct_report(tmp_path, capsys):
    db = tmp_path / "stats.db"
    calls, tasks, _runs = _seed(db)
    code = main(["stats", "explain", "build", "--db", str(db), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out == explain_report(calls, tasks, "build")
    assert out["by_failure_class"] == {"tool_error": 1}


def test_cli_stats_explain_unknown_role_reports_zero_rows_not_a_raise(tmp_path, capsys):
    db = tmp_path / "stats.db"
    _seed(db)
    code = main(["stats", "explain", "no-such-role", "--db", str(db), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["n_calls"] == 0
    assert out["coverage"] == 0.0


def test_cli_stats_series_json_matches_the_direct_report(tmp_path, capsys):
    db = tmp_path / "stats.db"
    calls, tasks, runs = _seed(db)
    code = main(["stats", "series", "--db", str(db), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out == series_report(runs, tasks)


def test_cli_stats_coverage_json_matches_the_direct_report(tmp_path, capsys):
    db = tmp_path / "stats.db"
    calls, tasks, runs = _seed(db)
    code = main(["stats", "coverage", "--db", str(db), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out == coverage_report(calls, tasks, runs)
    assert len(out) == 7


def test_cli_stats_coverage_default_text_over_a_three_run_corpus_prints_seven_rows(tmp_path, capsys):
    db = tmp_path / "stats.db"
    conn = connect(db)
    for row in [
        {"run_id": "r1", "cartridge_sha": "abc", "provider_profile": "default", "host": "box1"},
        {"run_id": "r2", "cartridge_sha": "abc", "provider_profile": None, "host": "box1"},
        {"run_id": "r3", "cartridge_sha": "abc", "provider_profile": "default", "host": None},
    ]:
        _insert(conn, "runs", row)
    conn.commit()
    conn.close()
    code = main(["stats", "coverage", "--db", str(db)])
    out = capsys.readouterr().out
    assert code == 0
    assert out.strip().endswith("7 rows")
    assert "question=runs_with_provider_profile | known=2 | total=3 | fraction=0.6667" in out
    assert "question=runs_with_host | known=2 | total=3 | fraction=0.6667" in out


def _seed_two_regimes(db_path):
    conn = connect(db_path)
    for row in [
        {"run_id": "r1", "cartridge_sha": "abc", "provider_profile": "old-profile"},
        {"run_id": "r2", "cartridge_sha": "abc", "provider_profile": "new-profile"},
    ]:
        _insert(conn, "runs", row)
    for row in [
        {"run_id": "r1", "seq": 1, "role": "build", "model": "sonnet", "task_id": "r1:p1:t1",
         "join_confidence": "heuristic", "failure_class": None},
        {"run_id": "r2", "seq": 1, "role": "build", "model": "sonnet", "task_id": "r2:p1:t2",
         "join_confidence": "heuristic", "failure_class": None},
    ]:
        _insert(conn, "calls", row)
    for row in [
        {"run_id": "r1", "task_id": "r1:p1:t1", "attempt": 1, "outcome": "landed", "cost_usd": 1.0},
        {"run_id": "r2", "task_id": "r2:p1:t2", "attempt": 1, "outcome": "landed", "cost_usd": 2.0},
    ]:
        _insert(conn, "tasks", row)
    conn.commit()
    conn.close()


def test_cli_stats_roles_provider_profile_filter_narrows_to_the_matching_regime(tmp_path, capsys):
    db = tmp_path / "stats.db"
    _seed_two_regimes(db)
    code = main(["stats", "roles", "--db", str(db), "--provider-profile", "new-profile", "--json"])
    [row] = json.loads(capsys.readouterr().out)
    assert code == 0
    assert row["n_calls"] == 1
    assert row["regimes"] == [{"cartridge_sha": "abc", "provider_profile": "new-profile"}]


def test_cli_stats_roles_unfiltered_flags_a_group_spanning_two_regimes(tmp_path, capsys):
    db = tmp_path / "stats.db"
    _seed_two_regimes(db)
    code = main(["stats", "roles", "--db", str(db), "--json"])
    [row] = json.loads(capsys.readouterr().out)
    assert code == 0
    assert row["n_calls"] == 2
    assert row["regimes"] == [
        {"cartridge_sha": "abc", "provider_profile": "new-profile"},
        {"cartridge_sha": "abc", "provider_profile": "old-profile"},
    ]


def _seed_two_shas(db_path):
    conn = connect(db_path)
    for row in [
        {"run_id": "r1", "cartridge_sha": "sha-old", "provider_profile": "p1"},
        {"run_id": "r2", "cartridge_sha": "sha-new", "provider_profile": "p1"},
    ]:
        _insert(conn, "runs", row)
    for row in [
        {"run_id": "r1", "task_id": "r1:p1:t1", "attempt": 1, "outcome": "landed", "cost_usd": 1.0},
        {"run_id": "r2", "task_id": "r2:p1:t2", "attempt": 1, "outcome": "landed", "cost_usd": 2.0},
    ]:
        _insert(conn, "tasks", row)
    conn.commit()
    conn.close()


def test_cli_stats_series_cartridge_sha_filter_narrows_to_the_matching_regime(tmp_path, capsys):
    db = tmp_path / "stats.db"
    _seed_two_shas(db)
    code = main(["stats", "series", "--db", str(db), "--cartridge-sha", "sha-old", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [r["run_id"] for r in rows] == ["r1"]


@pytest.mark.parametrize("cmd", ["roles", "explain", "series", "coverage", "bounds"])
def test_stats_subcommand_help_exits_zero(cmd):
    argv = ["stats", cmd, "role", "--help"] if cmd == "explain" else ["stats", cmd, "--help"]
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(argv)
    assert exc_info.value.code == 0


def _seed_bounds(db_path):
    conn = connect(db_path)
    calls = [
        {"run_id": "r1", "seq": i, "role": "build", "model": "sonnet", "cost_usd": float(i + 1), "failure_class": None}
        for i in range(20)
    ]
    for row in calls:
        _insert(conn, "calls", row)
    conn.commit()
    conn.close()
    return calls


def _seed_routing_and_provider_profiles(tmp_path, role_budget_usd):
    provider = tmp_path / "provider.yaml"
    provider.write_text(f"role_budget_usd:\n  build: {role_budget_usd}\n")
    routing = tmp_path / "profile.yaml"
    routing.write_text(f"provider_profile: {provider}\n")
    return routing


def _no_such_profile(tmp_path, monkeypatch):
    """Points the default routing-profile resolution (`_profile_path`'s
    `$AGENT_TOOLS_PROFILE` fallback) at a path that does not exist, so a test
    asserting a null ceiling never depends on whether the machine running it
    happens to have `~/.config/agent-tools/profile.yaml`."""
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(tmp_path / "no-such-profile.yaml"))


def test_cli_stats_bounds_json_matches_the_direct_report(tmp_path, capsys, monkeypatch):
    db = tmp_path / "stats.db"
    calls = _seed_bounds(db)
    _no_such_profile(tmp_path, monkeypatch)
    code = main(["stats", "bounds", "--db", str(db), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out == bounds_report(calls, lambda role, model: None)
    [row] = out
    assert row["n"] == 20 and row["strict"] == 10.0 and row["moderate"] == 19.0 and row["liberal"] == 57.0
    assert row["ceiling"] is None


def test_cli_stats_bounds_default_text_uses_render_capped(tmp_path, capsys, monkeypatch):
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    _no_such_profile(tmp_path, monkeypatch)
    code = main(["stats", "bounds", "--db", str(db)])
    out = capsys.readouterr().out
    assert code == 0
    assert "role=build" in out
    assert len(out) // 4 <= 300


def test_cli_stats_bounds_level_keeps_only_the_requested_candidate(tmp_path, capsys, monkeypatch):
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    _no_such_profile(tmp_path, monkeypatch)
    code = main(["stats", "bounds", "--db", str(db), "--json", "--level", "strict"])
    [row] = json.loads(capsys.readouterr().out)
    assert code == 0
    assert row["strict"] == 10.0
    assert "moderate" not in row
    assert "liberal" not in row


def test_cli_stats_bounds_profile_flag_resolves_the_provider_profile_and_flags_censored(tmp_path, capsys):
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    routing = _seed_routing_and_provider_profiles(tmp_path, role_budget_usd=5.0)
    code = main(["stats", "bounds", "--db", str(db), "--json", "--profile", str(routing)])
    [row] = json.loads(capsys.readouterr().out)
    assert code == 0
    assert row["ceiling"] == 5.0
    assert row["censored"] is True


def test_cli_stats_bounds_a_per_tier_budget_usd_mapping_falls_back_to_the_standard_tier(tmp_path, capsys):
    """A real provider profile declares budget_usd per TIER; the first live run crashed on `0.95 * mapping`."""
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    provider = tmp_path / "provider.yaml"
    provider.write_text("budget_usd:\n  cheap: 0.15\n  standard: 0.35\n  deep: 0.80\nrole_budget_usd:\n  review_charter: 0.80\n")
    routing = tmp_path / "profile.yaml"
    routing.write_text(f"provider_profile: {provider}\n")
    code = main(["stats", "bounds", "--db", str(db), "--json", "--profile", str(routing)])
    [row] = json.loads(capsys.readouterr().out)
    assert code == 0
    assert row["ceiling"] == 0.35


def _write_full_provider(tmp_path, *, role_budget_usd="", tier_overrides="", defaults=""):
    provider = tmp_path / "provider.yaml"
    provider.write_text(
        "budget_usd:\n  cheap: 0.15\n  standard: 0.35\n  deep: 0.80\n"
        f"role_budget_usd:\n{role_budget_usd}"
        f"tier_overrides:\n{tier_overrides}"
        f"defaults:\n{defaults}"
    )
    routing = tmp_path / "profile.yaml"
    routing.write_text(f"provider_profile: {provider}\n")
    return routing


def test_cli_stats_bounds_role_budget_usd_wins_over_tier_maps(tmp_path, capsys):
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    routing = _write_full_provider(
        tmp_path,
        role_budget_usd="  build: 9.0\n",
        tier_overrides="  build: cheap\n",
        defaults="  build: deep\n",
    )
    code = main(["stats", "bounds", "--db", str(db), "--json", "--profile", str(routing)])
    [row] = json.loads(capsys.readouterr().out)
    assert code == 0
    assert row["ceiling"] == 9.0


def test_cli_stats_bounds_tier_overrides_resolves_the_tier_then_budget_usd(tmp_path, capsys):
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    routing = _write_full_provider(
        tmp_path,
        role_budget_usd="  other_role: 9.0\n",
        tier_overrides="  build: cheap\n",
        defaults="  build: deep\n",
    )
    code = main(["stats", "bounds", "--db", str(db), "--json", "--profile", str(routing)])
    [row] = json.loads(capsys.readouterr().out)
    assert code == 0
    assert row["ceiling"] == 0.15


def test_cli_stats_bounds_defaults_resolves_the_tier_when_no_override(tmp_path, capsys):
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    routing = _write_full_provider(
        tmp_path,
        role_budget_usd="  other_role: 9.0\n",
        tier_overrides="  other_role: cheap\n",
        defaults="  build: deep\n",
    )
    code = main(["stats", "bounds", "--db", str(db), "--json", "--profile", str(routing)])
    [row] = json.loads(capsys.readouterr().out)
    assert code == 0
    assert row["ceiling"] == 0.80


def test_cli_stats_bounds_role_in_no_map_falls_back_to_standard(tmp_path, capsys):
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    routing = _write_full_provider(
        tmp_path,
        role_budget_usd="  other_role: 9.0\n",
        tier_overrides="  other_role: cheap\n",
        defaults="  other_role: deep\n",
    )
    code = main(["stats", "bounds", "--db", str(db), "--json", "--profile", str(routing)])
    [row] = json.loads(capsys.readouterr().out)
    assert code == 0
    assert row["ceiling"] == 0.35


def test_cli_stats_bounds_default_invocation_resolves_a_real_ceiling_via_agent_tools_profile(tmp_path, capsys, monkeypatch):
    """No `--profile` on the command line, the doc's own signature
    (docs/design/cost-bounds.md §2: `cox stats bounds [--json] [--level ...]`)
    — resolution falls through to `$AGENT_TOOLS_PROFILE`, the same chain
    `route context`/`setup doctor` use."""
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    routing = _seed_routing_and_provider_profiles(tmp_path, role_budget_usd=5.0)
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(routing))
    code = main(["stats", "bounds", "--db", str(db), "--json"])
    [row] = json.loads(capsys.readouterr().out)
    assert code == 0
    assert row["ceiling"] == 5.0
    assert row["censored"] is True


def test_cli_stats_bounds_write_puts_generated_db_and_rows_on_disk(tmp_path, capsys, monkeypatch):
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    _no_such_profile(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    out_path = tmp_path / "bounds.json"
    code = main(["stats", "bounds", "--db", str(db), "--write", str(out_path)])
    capsys.readouterr()
    assert code == 0
    written = json.loads(out_path.read_text())
    assert set(written) == {"generated", "db", "rows"}
    assert written["db"] == str(db)
    assert len(written["rows"]) == 1


def test_cli_stats_bounds_write_refuses_a_path_outside_the_repo_checkout(tmp_path, capsys, monkeypatch):
    db = tmp_path / "stats.db"
    _seed_bounds(db)
    _no_such_profile(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "escaped.json"
    code = main(["stats", "bounds", "--db", str(db), "--write", str(outside)])
    out = capsys.readouterr().out
    assert code == 2
    assert "outside the repo checkout" in out
    assert not outside.exists()
