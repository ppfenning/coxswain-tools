import json
from datetime import UTC, datetime

from agent_tools import cli
from agent_tools.pacing import Window

# Fixed in the past so `_usage_assess`'s own `datetime.now(UTC)` always falls
# after `_END`, pinning `elapsed_fraction` at 1.0 regardless of wall-clock
# time when the test runs.
_START = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
_END = datetime(2020, 1, 1, 10, 0, 0, tzinfo=UTC)


def _unmeasured_window() -> Window:
    return Window(start=_START, end=_END, spent_usd=1.0, ceiling_usd=None,
                  burn_usd_per_hour=0.5, runs_in_flight=1)


def _stopped_window() -> Window:
    return Window(start=_START, end=_END, spent_usd=95.0, ceiling_usd=10.0,
                  burn_usd_per_hour=90.0, runs_in_flight=3)


def test_go_against_an_unmeasured_window_exits_zero_and_names_the_verdict(capsys, monkeypatch):
    monkeypatch.setattr(cli.usage_window, "gather", lambda *a, **k: _unmeasured_window())
    code = cli.main(["usage", "assess", "--runs-dir", "runs"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("go: ")


def test_stop_against_an_exhausted_window_exits_four(capsys, monkeypatch):
    monkeypatch.setattr(cli.usage_window, "gather", lambda *a, **k: _stopped_window())
    code = cli.main(["usage", "assess"])
    out = capsys.readouterr().out
    assert code == 4
    assert out.startswith("stop: ")


def test_json_flag_prints_the_full_assessment(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(cli.usage_window, "gather", lambda *a, **k: _unmeasured_window())
    code = cli.main(["usage", "assess", "--json", "--runs-dir", str(tmp_path)])
    d = json.loads(capsys.readouterr().out)
    assert code == 0
    assert d["verdict"] == "go" and d["hold_until"] is None
    # No policy.pacing.json in tmp_path: falls back to DEFAULT_POLICY's own
    # ladders (deep/high), not a guessed tighter one.
    assert d["tier_ceiling"] == "deep" and d["effort_ceiling"] == "high"


def test_a_resolved_policy_file_in_the_runs_dir_changes_the_reported_ceilings(capsys, monkeypatch, tmp_path):
    (tmp_path / "policy.pacing.json").write_text(
        json.dumps({"tier_ladder": ["cheap"], "effort_ladder": ["low"]}), encoding="utf-8"
    )
    monkeypatch.setattr(cli.usage_window, "gather", lambda *a, **k: _unmeasured_window())
    code = cli.main(["usage", "assess", "--json", "--runs-dir", str(tmp_path)])
    d = json.loads(capsys.readouterr().out)
    assert code == 0
    assert d["tier_ceiling"] == "cheap" and d["effort_ceiling"] == "low"
