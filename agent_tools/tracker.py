"""The tracker registry: where `cox route sync` mirrors the work store.

A tracker is a module. `github-projects` is `agent_tools.route_sync_gh`;
`none` mirrors nothing and is the default. A package can register more under
the `coxswain.trackers` entry-point group; such a tracker resolves here and
`route sync` drives it through `Tracker.sync`.

The name comes from `<runs_dir>/policy.tracker.json` when present, because a
run drops one there to pause sync. Otherwise it is the profile's `tracker` key,
then `DEFAULT`.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from agent_tools import route_sync

DEFAULT = "none"
ENTRY_POINT_GROUP = "coxswain.trackers"

_NONE = types.SimpleNamespace()


class Tracker(Protocol):
    """A registered tracker, driven by `route sync` through `sync`.

    It returns ok and the lines to print, and writes nothing when `dry_run` is true.
    """

    def sync(self, workspace: str, items: Sequence[route_sync.Item], *, dry_run: bool) -> tuple[bool, list[str]]: ...


def _policy_tracker(runs_dir: Path) -> str | None:
    try:
        raw = json.loads((runs_dir / "policy.tracker.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = raw.get("tracker") if isinstance(raw, dict) else None
    return name if isinstance(name, str) and name else None


def tracker_name(profile: Mapping, runs_dir: Path) -> str:
    return _policy_tracker(runs_dir) or profile.get("tracker") or DEFAULT


def tracker_for(name: str) -> object | None:
    """`none` gives an empty stand-in so callers can tell it from an unknown name, which gives None."""
    if name == "none":
        return _NONE
    if name == "github-projects":
        return importlib.import_module("agent_tools.route_sync_gh")
    registered = [ep for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]
    return registered[0].load() if registered else None
