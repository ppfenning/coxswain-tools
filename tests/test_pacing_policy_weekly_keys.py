from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_tools import cli, usage_window

DEFAULT = usage_window.DEFAULT_POLICY


def _write(tmp_path: Path, doc: dict) -> None:
    (tmp_path / "policy.pacing.json").write_text(json.dumps(doc))


def test_both_keys_are_carried(tmp_path: Path) -> None:
    _write(tmp_path, {"weekly_target_fraction": 0.8, "weekly_pace_lanes": [[0.9, 1.0], [1.2, 0.4]]})
    policy = cli._resolved_pacing_policy(tmp_path)
    assert policy.weekly_target_fraction == 0.8
    assert policy.weekly_pace_lanes == ((0.9, 1.0), (1.2, 0.4))


def test_neither_key_gives_defaults_and_other_keys_still_read(tmp_path: Path) -> None:
    _write(tmp_path, {"hard_stop_fraction": 0.5})
    policy = cli._resolved_pacing_policy(tmp_path)
    assert policy.weekly_target_fraction == DEFAULT.weekly_target_fraction
    assert policy.weekly_pace_lanes == DEFAULT.weekly_pace_lanes
    assert policy.hard_stop_fraction == 0.5


@pytest.mark.parametrize(
    "lanes", ["0.8,1.0", [[0.9, 1.0, 0.5]], [[0.9, True]], [[float("nan"), 1.0]], [[0.9, float("inf")]]],
)
def test_malformed_lanes_give_the_default_ladder(tmp_path: Path, lanes: object) -> None:
    _write(tmp_path, {"weekly_pace_lanes": lanes})
    assert cli._resolved_pacing_policy(tmp_path).weekly_pace_lanes == DEFAULT.weekly_pace_lanes


def test_empty_lanes_mean_no_throttling_not_the_default(tmp_path: Path) -> None:
    _write(tmp_path, {"weekly_pace_lanes": []})
    assert cli._resolved_pacing_policy(tmp_path).weekly_pace_lanes == ()


@pytest.mark.parametrize("target", ["0.8", True, None, float("nan"), float("inf")])
def test_malformed_target_gives_the_default(tmp_path: Path, target: object) -> None:
    _write(tmp_path, {"weekly_target_fraction": target, "hard_stop_fraction": 0.5})
    policy = cli._resolved_pacing_policy(tmp_path)
    assert policy.weekly_target_fraction == DEFAULT.weekly_target_fraction
    assert policy.hard_stop_fraction == 0.5


def test_one_bad_pair_discards_the_whole_ladder(tmp_path: Path) -> None:
    _write(tmp_path, {"weekly_pace_lanes": [[0.9, 1.0], [1.2, "x"]]})
    assert cli._resolved_pacing_policy(tmp_path).weekly_pace_lanes == DEFAULT.weekly_pace_lanes
