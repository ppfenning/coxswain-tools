import json

from agent_tools import records
from agent_tools.cli import main


def _usage(cost=1.0, turns=50, calls=10, input_total=1000, cache_read=400, roles=()):
    return {
        "run_id": "?",
        "summary": {"calls": calls, "cost_usd": cost, "turns": turns, "input_total": input_total, "cache_read_tokens": cache_read},
        "calls": [{"role": r} for r in roles],
    }


def _manifest(ts, completed=0, quarantined=0, cartridge_sha="abcdef1234567890", provider_profile="acme"):
    return {"ts": ts, "cartridge_sha": cartridge_sha, "provider_profile": provider_profile,
            "totals": {"completed": completed, "quarantined": quarantined}}


def test_series_row_sums_completed_and_quarantined_across_two_phase_manifests():
    usage = _usage(cost=2.0, calls=4, turns=20)
    manifests = [_manifest("2026-09-01T10:00:00Z", completed=2, quarantined=1), _manifest("2026-09-02T10:00:00Z", completed=3, quarantined=0)]
    row = records.series_row("r1", usage, manifests)
    assert row["tasks_landed"] == 5 and row["quarantined"] == 1
    assert row["date"] == "2026-09-01" and row["cartridge_sha"] == "abcdef123456"


def test_series_row_takes_cartridge_sha_and_provider_profile_from_the_earliest_ts_manifest():
    usage = _usage(cost=1.0)
    manifests = [
        _manifest("2026-09-02T10:00:00Z", cartridge_sha="later0000000", provider_profile="later-profile"),
        _manifest("2026-09-01T10:00:00Z", cartridge_sha="earlier000000", provider_profile="earlier-profile"),
    ]
    row = records.series_row("r1", usage, manifests)
    assert row["date"] == "2026-09-01"
    assert row["cartridge_sha"] == "earlier000000"[:12] and row["provider_profile"] == "earlier-profile"


def test_series_row_cache_share_arithmetic():
    usage = _usage(input_total=1000, cache_read=250)
    row = records.series_row("r1", usage, [])
    assert row["cache_share"] == 0.25


def test_series_row_with_usage_and_no_manifests():
    usage = _usage(cost=1.5, calls=3, turns=10)
    row = records.series_row("r1", usage, [])
    assert row["date"] == "" and row["cartridge_sha"] == "" and row["provider_profile"] == ""
    assert row["tasks_landed"] == 0 and row["cost_per_landed"] is None and row["cost_usd"] == 1.5


def test_series_row_with_manifests_and_no_usage():
    manifests = [_manifest("2026-09-01T00:00:00Z", completed=2)]
    row = records.series_row("r1", None, manifests)
    assert row["calls"] == 0 and row["turns"] == 0 and row["cost_usd"] == 0.0 and row["cache_share"] == 0.0
    assert row["tasks_landed"] == 2 and row["cost_per_landed"] == 0.0


def test_series_row_cost_per_landed_is_none_at_zero_landed():
    usage = _usage(cost=5.0)
    row = records.series_row("r1", usage, [_manifest("2026-09-01T00:00:00Z", completed=0)])
    assert row["tasks_landed"] == 0 and row["cost_per_landed"] is None and row["review_rounds"] == 0.0


def test_series_row_review_rounds_counts_review_and_arbitrate_roles():
    usage = _usage(roles=["build", "review_charter", "review_adversary", "arbitrate", "build"])
    row = records.series_row("r1", usage, [_manifest("2026-09-01T00:00:00Z", completed=3)])
    assert row["review_rounds"] == 1.0


def test_series_groups_by_run_id_and_handles_colon_filename():
    files = {
        "r1.usage.json": json.dumps(_usage(cost=1.0, calls=2, turns=5)),
        "r1:build.json": json.dumps(_manifest("2026-09-01T00:00:00Z", completed=1)),
        "r1:review.json": json.dumps(_manifest("2026-09-01T01:00:00Z", completed=2)),
    }
    rows = records.series(files)
    assert len(rows) == 1
    assert rows[0]["run"] == "r1" and rows[0]["tasks_landed"] == 3


def test_series_sorts_by_date_then_run():
    files = {
        "b.usage.json": json.dumps(_usage()),
        "b:p.json": json.dumps(_manifest("2026-09-02T00:00:00Z")),
        "a.usage.json": json.dumps(_usage()),
        "a:p.json": json.dumps(_manifest("2026-09-01T00:00:00Z")),
    }
    rows = records.series(files)
    assert [r["run"] for r in rows] == ["a", "b"]


def test_series_totals_counts_runs_landing_nothing():
    rows = [
        {"cost_usd": 1.0, "tasks_landed": 2, "quarantined": 0},
        {"cost_usd": 3.0, "tasks_landed": 0, "quarantined": 1},
    ]
    totals = records.series_totals(rows)
    assert totals["runs"] == 2 and totals["runs_landing_nothing"] == 1
    assert totals["cost_usd"] == 4.0 and totals["tasks_landed"] == 2 and totals["cost_per_landed"] == 2.0


def _write_run(tmp_path, run_id, cost, completed, ts):
    (tmp_path / f"{run_id}.usage.json").write_text(json.dumps(_usage(cost=cost)))
    (tmp_path / f"{run_id}:build.json").write_text(json.dumps(_manifest(ts, completed=completed)))


def test_cli_runs_series_prints_table_with_run_id(tmp_path, capsys):
    _write_run(tmp_path, "run-42", 1.5, 2, "2026-09-01T00:00:00Z")
    rc = main(["runs", "series", "--runs-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "run-42" in out and "totals:" in out


def test_cli_runs_series_json_parses(tmp_path, capsys):
    _write_run(tmp_path, "run-1", 1.0, 1, "2026-09-01T00:00:00Z")
    rc = main(["runs", "series", "--runs-dir", str(tmp_path), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert doc["rows"][0]["run"] == "run-1" and doc["totals"]["runs"] == 1


def test_cli_runs_series_append_writes_once_and_skips_existing(tmp_path, capsys):
    _write_run(tmp_path, "run-1", 1.0, 1, "2026-09-01T00:00:00Z")
    out_file = tmp_path / "series.jsonl"
    main(["runs", "series", "--runs-dir", str(tmp_path), "--append", str(out_file)])
    capsys.readouterr()
    lines = out_file.read_text().splitlines()
    assert len(lines) == 1

    _write_run(tmp_path, "run-2", 2.0, 2, "2026-09-02T00:00:00Z")
    main(["runs", "series", "--runs-dir", str(tmp_path), "--append", str(out_file)])
    capsys.readouterr()
    lines2 = out_file.read_text().splitlines()
    assert len(lines2) == 2
    assert {json.loads(l)["run"] for l in lines2} == {"run-1", "run-2"}


def test_series_row_undated_manifests_give_the_same_row_regardless_of_order():
    usage = _usage(cost=1.0)
    m1 = _manifest("", completed=1, cartridge_sha="aaaaaaaaaaaa", provider_profile="alpha")
    m1.pop("ts")
    m2 = _manifest("", completed=2, cartridge_sha="bbbbbbbbbbbb", provider_profile="beta")
    m2.pop("ts")
    row_forward = records.series_row("r1", usage, [m1, m2])
    row_reversed = records.series_row("r1", usage, [m2, m1])
    assert row_forward["date"] == row_reversed["date"]
    assert row_forward["cartridge_sha"] == row_reversed["cartridge_sha"]
    assert row_forward["provider_profile"] == row_reversed["provider_profile"]


def test_series_row_a_dated_manifest_always_wins_over_an_undated_one():
    usage = _usage(cost=1.0)
    dated = _manifest("2026-09-01T00:00:00Z", completed=1, cartridge_sha="dated0000000", provider_profile="dated")
    undated = _manifest("", completed=2, cartridge_sha="undated00000", provider_profile="undated")
    undated.pop("ts")
    row_forward = records.series_row("r1", usage, [dated, undated])
    row_reversed = records.series_row("r1", usage, [undated, dated])
    for row in (row_forward, row_reversed):
        assert row["date"] == "2026-09-01"
        assert row["cartridge_sha"] == "dated0000000" and row["provider_profile"] == "dated"


def test_series_row_gives_the_same_figures_from_calls_only_and_from_a_precomputed_summary():
    usage_from_calls = {
        "run_id": "r1",
        "calls": [
            {"role": "build", "cost_usd": 1.0, "turns": 5, "input_total": 500, "cache_read_tokens": 125},
            {"role": "review_charter", "cost_usd": 1.0, "turns": 5, "input_total": 500, "cache_read_tokens": 125},
        ],
    }
    usage_from_summary = {
        "run_id": "r1",
        "summary": {"calls": 2, "cost_usd": 2.0, "turns": 10, "input_total": 1000, "cache_read_tokens": 250},
        "calls": [],
    }
    row_calls = records.series_row("r1", usage_from_calls, [])
    row_summary = records.series_row("r1", usage_from_summary, [])
    assert row_calls["calls"] == row_summary["calls"] == 2
    assert row_calls["cost_usd"] == row_summary["cost_usd"] == 2.0
    assert row_calls["turns"] == row_summary["turns"] == 10
    assert row_calls["cache_share"] == row_summary["cache_share"] == 0.25
