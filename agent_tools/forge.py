"""The forge registry: where a land's push, pull request, checks and merge go.

A forge is a module that defines these five functions. Each returns
`(ok: bool, detail: str)` except the first.

    find_open_prs(repo: Path, branch: str) -> list[int] | str
        Numbers of the open PRs whose head is `branch`, or the reason they
        could not be listed.
    push(repo: Path, branch: str)
        Publish `branch` from `repo`; `detail` is the branch on success.
    open_pr(repo: Path, title: str, body: str)
        Open a PR for the checked-out branch; `detail` is its address.
    wait_checks(repo: Path, timeout_s: float)
        Block until the PR's checks are green, failed, or `timeout_s` passes.
    merge(repo: Path, step: dict)
        Merge the PR and delete its branch; `step` is the plan step.

The profile's `forge` key names the forge and `DEFAULT` applies when it is
absent. `agent_tools.forge_<name>` is built in; a package can register more
under the `coxswain.forges` entry-point group.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from collections.abc import Mapping

DEFAULT = "local"
ENTRY_POINT_GROUP = "coxswain.forges"


def forge_name(profile: Mapping) -> str:
    return profile.get("forge") or DEFAULT


def forge_for(name: str):
    """The built-in `agent_tools.forge_<name>`, else the forge an installed package registers under `coxswain.forges`; None for neither."""
    try:
        return importlib.import_module(f"agent_tools.forge_{name}")
    except ImportError:
        pass
    registered = [ep for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]
    return registered[0].load() if registered else None
