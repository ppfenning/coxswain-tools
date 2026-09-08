import json

import pytest

from agent_tools.cli import build_parser, main
from agent_tools.stats_query import explain_report, roles_report, series_report
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


@pytest.mark.parametrize("cmd", ["roles", "explain", "series"])
def test_stats_subcommand_help_exits_zero(cmd):
    argv = ["stats", cmd, "role", "--help"] if cmd == "explain" else ["stats", cmd, "--help"]
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(argv)
    assert exc_info.value.code == 0
