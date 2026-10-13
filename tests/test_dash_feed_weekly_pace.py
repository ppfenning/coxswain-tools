"""`dash_feed._spend` carries the weekly target and the pace verdict on every path."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agent_tools import dash_feed, usage_meter, usage_window

NOW_DT = datetime(2026, 10, 9, 12, tzinfo=UTC)
NOW = NOW_DT.isoformat()


def _meter(seven_pct: float, resets_at: datetime) -> usage_meter.Meter:
    return usage_meter.Meter(
        five_hour=usage_meter.MeterEntry(used_percentage=8, resets_at=NOW_DT + timedelta(hours=2)),
        seven_day=usage_meter.MeterEntry(used_percentage=seven_pct, resets_at=resets_at),
        observed_at=NOW_DT - timedelta(minutes=5),
    )


def _spend(tmp_path, local):
    return dash_feed._spend(
        tmp_path, NOW, read_local=lambda: local, read_store=lambda runs_dir: None, read_reset=lambda: None,
    )


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    (tmp_path / "policy.pacing.json").write_text(json.dumps({"weekly_target_fraction": 0.9}))
    monkeypatch.setattr(dash_feed, "_profile_ceilings", lambda: (100.0, 100.0))
    monkeypatch.setattr(usage_meter, "implied_ceiling", lambda kind, now, *a, **k: None)

    def gather(runs_dir, now, *, ceiling_usd=None):
        return usage_window.Window(
            start=now - timedelta(hours=5), end=now, spent_usd=0.0, ceiling_usd=ceiling_usd, burn_usd_per_hour=0.0,
            runs_in_flight=0,
        )

    def gather_weekly(runs_dir, now, *, weekly_ceiling_usd=None, start=None):
        return usage_window.Window(
            start=now - timedelta(days=3.5), end=now + timedelta(days=3.5), spent_usd=45.0,
            ceiling_usd=weekly_ceiling_usd, burn_usd_per_hour=0.0, runs_in_flight=0,
        )

    monkeypatch.setattr(dash_feed.usage_window, "gather", gather)
    monkeypatch.setattr(dash_feed.usage_window, "gather_weekly", gather_weekly)


def test_a_fresh_meter_at_mid_week_reads_the_target_and_full(tmp_path):
    spend = _spend(tmp_path, _meter(45, NOW_DT + timedelta(days=3, hours=12)))

    assert spend["weekly_target_fraction"] == pytest.approx(0.45)
    assert spend["weekly_pace"] == "full"


def test_a_fresh_meter_two_percent_into_the_week_is_not_judged(tmp_path):
    spend = _spend(tmp_path, _meter(1, NOW_DT + timedelta(days=7) * 0.98))

    assert spend["weekly_target_fraction"] == 0.0
    assert spend["weekly_pace"] == "not_judged"


def test_the_estimate_path_carries_both_keys(tmp_path):
    spend = _spend(tmp_path, None)

    assert spend["spend_source"] == "estimate"
    assert spend["weekly_target_fraction"] == pytest.approx(0.45)
    assert spend["weekly_pace"] == "full"
