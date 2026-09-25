import json
import sys

import test_lake_sync_cli as sync_cli
from test_lake_sync_cli import _run, needs_lake

# The sync test's fixture, re-exported by assignment: pytest finds it as a module attribute, and an import of `env`
# would be shadowed by every test parameter of the same name.
lake_env = sync_cli.env


def _argv(lake_env, *words):
    """`cox lake <words>` against the same profile and runs dir the sync fixture names."""
    _, sync = lake_env
    return ["lake", *words, *sync[2:]]


def _synced(lake_env, capsys):
    rc, _ = _run(lake_env[1], capsys)
    assert rc == 0


@needs_lake
def test_query_prints_the_table(lake_env, capsys) -> None:
    _synced(lake_env, capsys)
    rc, out = _run(_argv(lake_env, "query", "SELECT count(*) AS n FROM runs"), capsys)
    assert rc == 0
    assert out.splitlines() == ["n", "-", "2"]


@needs_lake
def test_query_json_prints_parseable_rows(lake_env, capsys) -> None:
    _synced(lake_env, capsys)
    rc, out = _run(_argv(lake_env, "query", "SELECT count(*) AS n FROM runs", "--json"), capsys)
    assert rc == 0
    assert json.loads(out) == [{"n": 2}]


@needs_lake
def test_a_bad_sql_string_exits_non_zero_with_the_duckdb_message(lake_env, capsys) -> None:
    _synced(lake_env, capsys)
    rc, out = _run(_argv(lake_env, "query", "SELEKT 1"), capsys)
    assert rc != 0
    assert "syntax error" in out.lower()
    assert "Traceback" not in out


@needs_lake
def test_doctor_on_the_synced_lake_exits_zero_with_one_line_per_check(lake_env, capsys) -> None:
    _synced(lake_env, capsys)
    rc, out = _run(_argv(lake_env, "doctor"), capsys)
    assert rc == 0
    assert "ok catalog: answers at" in out
    # The fixture store has no node_calls rows and no trace files, so those two tables have no snapshot: warn, not fail.
    assert "warn table traces: no snapshot" in out
    assert out.splitlines()[-1] == "verdict: warn"


@needs_lake
def test_doctor_json_carries_the_verdict_and_the_checks(lake_env, capsys) -> None:
    _synced(lake_env, capsys)
    rc, out = _run(_argv(lake_env, "doctor", "--json"), capsys)
    report = json.loads(out)
    assert rc == 0
    assert report["verdict"] == "warn"
    assert {"name": "catalog", "status": "ok"}.items() <= report["checks"][0].items()


@needs_lake
def test_doctor_on_an_empty_catalog_prints_the_sync_hint(lake_env, capsys) -> None:
    rc, out = _run(_argv(lake_env, "doctor"), capsys)
    assert rc == 0
    assert "warn namespace coxswain: not found; run cox lake sync" in out


@needs_lake
def test_a_missing_extra_names_it_and_prints_no_traceback(lake_env, capsys, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pyiceberg.catalog.sql", None)
    rc, out = _run(_argv(lake_env, "query", "SELECT 1"), capsys)
    assert rc == 2
    assert "coxswain-tools[lake]" in out
    assert "Traceback" not in out
    rc, out = _run(_argv(lake_env, "doctor"), capsys)
    assert rc == 1
    assert "coxswain-tools[lake]" in out
