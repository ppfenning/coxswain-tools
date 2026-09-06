from datetime import UTC, datetime

from agent_tools.pacing import assess
from agent_tools.usage_window import DEFAULT_POLICY, gather, window_from

_NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)


def _active_block(cost=30.0, active=True, **overrides):
    block = {
        "isActive": active,
        "startTime": (_NOW.replace(hour=_NOW.hour - 1)).isoformat(),
        "endTime": (_NOW.replace(hour=_NOW.hour + 3)).isoformat(),
        "costUSD": cost,
    }
    block.update(overrides)
    return block


def test_window_from_uses_the_active_blocks_own_spend_and_burn():
    blocks_json = {"blocks": [_active_block()]}
    window = window_from(blocks_json, [], _NOW)
    assert window.spent_usd == 30.0
    assert window.burn_usd_per_hour == 30.0 / 4  # 4-hour block span
    assert window.ceiling_usd is None


def test_empty_blocks_list_falls_back_to_usage_files():
    inside = _NOW.replace(hour=11)
    window = window_from({"blocks": []}, [(inside, {"cost_usd": 5.0})], _NOW, window_hours=5.0)
    assert window.spent_usd == 5.0
    assert window.start == _NOW - (window.end - window.start)


def test_a_block_present_but_not_marked_active_falls_back_to_usage_files():
    inside = _NOW.replace(hour=11)
    blocks_json = {"blocks": [_active_block(active=False)]}
    window = window_from(blocks_json, [(inside, {"cost_usd": 7.0})], _NOW, window_hours=5.0)
    assert window.spent_usd == 7.0


def test_a_usage_file_before_the_window_is_excluded():
    inside = _NOW.replace(hour=11)
    before = _NOW.replace(day=_NOW.day - 1)
    window = window_from(
        {"blocks": []},
        [(inside, {"cost_usd": 5.0}), (before, {"cost_usd": 100.0})],
        _NOW,
        window_hours=5.0,
    )
    assert window.spent_usd == 5.0
    assert window.runs_in_flight == 1


def test_a_garbage_block_is_ignored_not_raised():
    inside = _NOW.replace(hour=11)
    blocks_json = {"blocks": [{"isActive": True, "startTime": "not-a-date", "costUSD": 30.0, "endTime": "also-not"}]}
    window = window_from(blocks_json, [(inside, {"cost_usd": 5.0})], _NOW, window_hours=5.0)
    assert window.spent_usd == 5.0


def test_a_block_missing_costusd_is_ignored_not_raised():
    inside = _NOW.replace(hour=11)
    blocks_json = {"blocks": [_active_block(cost="not-a-number")]}
    window = window_from(blocks_json, [(inside, {"cost_usd": 5.0})], _NOW, window_hours=5.0)
    assert window.spent_usd == 5.0


def test_fallback_window_spans_now_minus_window_hours_to_now():
    window = window_from({"blocks": []}, [], _NOW, window_hours=5.0)
    assert window.end == _NOW
    assert (window.end - window.start).total_seconds() / 3600 == 5.0


class _FakeResult:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def test_gather_uses_ccusage_when_it_answers_with_an_active_block():
    def fake_run(argv, **kwargs):
        assert argv[:3] == ["npx", "-y", "ccusage@latest"]
        return _FakeResult(0, f'{{"blocks": [{_block_json()}]}}')

    window = gather(runs_dir="/does/not/exist", now=_NOW, run=fake_run)
    assert window.spent_usd == 30.0


def _block_json():
    import json
    return json.dumps(_active_block())


def test_gather_falls_back_to_usage_files_when_the_launch_fails(tmp_path):
    (tmp_path / "one.usage.json").write_text(
        f'{{"cost_usd": 4.5, "ts": "{_NOW.replace(hour=11).isoformat()}"}}', encoding="utf-8"
    )

    def fake_run(argv, **kwargs):
        raise FileNotFoundError("npx not found")

    window = gather(runs_dir=tmp_path, now=_NOW, run=fake_run, window_hours=5.0)
    assert window.spent_usd == 4.5


def test_gather_falls_back_when_ccusage_returns_no_blocks(tmp_path):
    (tmp_path / "one.usage.json").write_text(
        f'{{"cost_usd": 2.0, "ts": "{_NOW.replace(hour=11).isoformat()}"}}', encoding="utf-8"
    )

    def fake_run(argv, **kwargs):
        return _FakeResult(0, '{"blocks": []}')

    window = gather(runs_dir=tmp_path, now=_NOW, run=fake_run, window_hours=5.0)
    assert window.spent_usd == 2.0


def test_ccusage_path_assessment_traces_back_to_the_blocks_own_numbers():
    window = window_from({"blocks": [_active_block()]}, [], _NOW)
    result = assess(window, DEFAULT_POLICY, _NOW)
    assert result.verdict == "go"
    assert "unmeasured" in result.reason
    hours_remaining = (window.end - _NOW).total_seconds() / 3600
    assert result.projected_total == window.spent_usd + window.burn_usd_per_hour * hours_remaining


def test_fallback_path_assessment_traces_back_to_the_in_window_sum_and_burn():
    inside = _NOW.replace(hour=11)
    window = window_from({"blocks": []}, [(inside, {"cost_usd": 12.0})], _NOW, window_hours=5.0)
    result = assess(window, DEFAULT_POLICY, _NOW)
    assert window.spent_usd == 12.0
    assert window.burn_usd_per_hour == 12.0 / 5.0
    assert result.projected_total == window.spent_usd  # now == window.end, no hours remaining


def test_default_policy_never_tightens_an_absent_pacing_policy():
    window = window_from({"blocks": []}, [], _NOW, window_hours=5.0)
    result = assess(window, DEFAULT_POLICY, _NOW)
    assert result.tier_ceiling == "deep"
    assert result.effort_ceiling == "high"
