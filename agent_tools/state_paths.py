"""Pure: which files are runtime state, which are records, and where each belongs. No I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath
from typing import Literal

# A per-run file is the run id plus one of these; a bare ".json" is not a runtime suffix.
RUNTIME_SUFFIXES = (".log", ".pid", ".launched.json", ".remote.json", ".fetched.json", ".usage.json")

# Names copied from their writers: chair.py LEASE_FILENAME and CHAIR_FILENAME, run_store.py and cli.py "land.jsonl",
# queue_export.py _BASELINE_RECORD and _HELD_RECORD, dash_feed.py "chair-loop.log", chair_run.py "notify-sent.json".
RUNTIME_NAMES = frozenset(
    {
        "checkpoints.jsonl",
        "chair.lease.json",
        "chair.json",
        "land.jsonl",
        ".export-baseline.json",
        ".export-held.json",
        "chair-loop.log",
        "notify-sent.json",
    }
)


@dataclass(frozen=True)
class StatePath:
    kind: Literal["runtime", "record"]
    folder: PurePath
    path: PurePath


def classify(name: str, workspace: PurePath, state_root: PurePath) -> StatePath:
    """Runtime files live under the state root; everything else is a record in the workspace runs folder."""
    if name in RUNTIME_NAMES or name.endswith(RUNTIME_SUFFIXES):
        return StatePath("runtime", state_root, state_root / name)
    folder = workspace / "runs"
    return StatePath("record", folder, folder / name)


def state_root(env: Mapping[str, str], home: PurePath) -> PurePath:
    """XDG_STATE_HOME/coxswain when set and non-empty, else .local/state/coxswain under home."""
    xdg = env.get("XDG_STATE_HOME", "")
    if xdg:
        return PurePath(xdg) / "coxswain"
    return home / ".local" / "state" / "coxswain"


def read_candidates(name: str, workspace: PurePath, state_root: PurePath) -> tuple[PurePath, ...]:
    """Where to look, in order: the state folder, then the old workspace runs path. A record has only the latter."""
    found = classify(name, workspace, state_root)
    old = workspace / "runs" / name
    if found.kind == "runtime":
        return (found.path, old)
    return (old,)
