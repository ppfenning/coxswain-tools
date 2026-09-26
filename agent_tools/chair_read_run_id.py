"""The run id a chair launch or relaunch is given, allocated the way `cox route launch` allocates it.

The numbering is `route.next_run_id`, called here and not copied. The core passes it the taken names;
the edge gathers them from the runs directory and the store, as `cli._taken_run_names` does.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path

from agent_tools import route, run_store
from agent_tools.chair_types import Action

__all__ = ["make_run_id", "next_run_id", "taken_names"]


def next_run_id(initiative: str, taken: frozenset[str]) -> str:
    """One past the highest `<initiative>-<n>` in `taken`; never a taken id, and never a gap fill."""
    return route.next_run_id(taken, initiative)


def bare_id(name: str) -> str:
    """`<id>.log`, `<id>.remote.json`, `<id>:<task>.json` and `<id>-trace` all belong to `<id>`."""
    return re.split(r"[.:]", name, maxsplit=1)[0].removesuffix("-trace")


def taken_names(dir_names: Iterable[str], store_ids: Iterable[str]) -> frozenset[str]:
    """Directory entries, the bare id each belongs to, and every store id."""
    names = list(dir_names)
    return frozenset([*names, *map(bare_id, names), *store_ids])


def make_run_id(runs_dir: Path) -> Callable[[Action], str]:
    """Edge. The `run_id(action)` closure: reads the runs directory and the store afresh on each call."""

    def run_id(action: Action) -> str:
        prefix = action.get("initiative") or next(iter(action.get("intake_ids") or []), "")
        names = [p.name for p in runs_dir.iterdir()] if runs_dir.is_dir() else []
        return next_run_id(prefix, taken_names(names, run_store.run_ids(runs_dir)))

    return run_id
