"""Plan and apply the move of runtime files out of the workspace into the state folder. Records are never moved."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Literal

from agent_tools.state_paths import classify


@dataclass(frozen=True)
class Step:
    kind: Literal["move", "conflict"]
    source: PurePath
    dest: PurePath


def plan_migration(
    runs_names: Iterable[str],
    root_names: Iterable[str],
    state_names: Collection[str],
    workspace: PurePath,
    state_root: PurePath,
) -> tuple[Step, ...]:
    """Runtime files only, runs folder before workspace root, each sorted. A taken destination is a conflict."""
    found = tuple(
        (workspace / folder / name if folder else workspace / name, classify(name, workspace, state_root))
        for folder, names in (("runs", runs_names), ("", root_names))
        for name in sorted(names)
    )
    runtime = tuple((source, hit.path) for source, hit in found if hit.kind == "runtime")
    return tuple(
        Step(
            "conflict" if dest.name in state_names or any(dest == earlier for _, earlier in runtime[:i]) else "move",
            source,
            dest,
        )
        for i, (source, dest) in enumerate(runtime)
    )


def apply_plan(plan: Iterable[Step]) -> None:
    """The imperative edge: rename each move into place, creating its folder. Conflicts are left untouched."""
    for step in plan:
        if step.kind == "move":
            Path(step.dest).parent.mkdir(parents=True, exist_ok=True)
            Path(step.source).rename(step.dest)
