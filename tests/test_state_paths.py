from dataclasses import FrozenInstanceError
from pathlib import Path, PurePath

import pytest

from agent_tools import state_paths
from agent_tools.state_paths import StatePath, classify, read_candidates, state_root

WS = PurePath("/w/workspace")
ROOT = PurePath("/s/coxswain")
HOME = PurePath("/home/user")  # the neutral stand-in .github/check_clean.py allows
DEFAULT_ROOT = HOME / ".local" / "state" / "coxswain"


@pytest.mark.parametrize(
    "name",
    [
        "r1.log",
        "r1.pid",
        "r1.launched.json",
        "r1.remote.json",
        "r1.fetched.json",
        "r1.usage.json",
        "checkpoints.jsonl",
        "chair.lease.json",
        "chair.json",
        "land.jsonl",
        ".export-baseline.json",
        ".export-held.json",
        "chair-loop.log",
        "notify-sent.json",
    ],
)
def test_runtime_names_land_under_the_state_root(name):
    assert classify(name, WS, ROOT) == StatePath("runtime", ROOT, ROOT / name)


@pytest.mark.parametrize("name", ["r1.summary.json", "r1.json", "something.else"])
def test_record_and_unknown_names_stay_in_workspace_runs(name):
    assert classify(name, WS, ROOT) == StatePath("record", WS / "runs", WS / "runs" / name)


def test_result_is_frozen():
    with pytest.raises(FrozenInstanceError):
        classify("r1.log", WS, ROOT).kind = "record"  # type: ignore[misc]


def test_state_root_uses_xdg_state_home_when_set():
    assert state_root({"XDG_STATE_HOME": "/x/state"}, HOME) == PurePath("/x/state/coxswain")


def test_state_root_defaults_under_home():
    assert state_root({}, HOME) == DEFAULT_ROOT


def test_state_root_ignores_an_empty_xdg_state_home():
    assert state_root({"XDG_STATE_HOME": ""}, HOME) == DEFAULT_ROOT


def test_runtime_read_candidates_try_state_then_old_workspace():
    assert read_candidates("r1.log", WS, ROOT) == (ROOT / "r1.log", WS / "runs" / "r1.log")


def test_record_read_candidates_are_the_workspace_path_only():
    assert read_candidates("r1.summary.json", WS, ROOT) == (WS / "runs" / "r1.summary.json",)


def test_module_does_not_import_os():
    source = Path(state_paths.__file__).read_text(encoding="utf-8")
    assert not [line for line in source.splitlines() if line.startswith(("import os", "from os"))]
