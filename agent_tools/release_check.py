"""The pure core of `cox dev release-check`: drifts computed from facts the
edge gathers. No check here reads a file, runs a command or touches the
network — `cli.py` gathers the facts named by `facts_plan` and calls in."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from pathlib import Path

from agent_tools import release


@dataclasses.dataclass(frozen=True)
class Drift:
    check: str
    a_file: str
    a_line: int | None
    b_file: str
    b_line: int | None
    correction: str


CHECKS: tuple[Callable[[Mapping], list[Drift]], ...] = ()


def run_checks(facts: Mapping, checks: tuple[Callable[[Mapping], list[Drift]], ...] | None = None) -> list[Drift]:
    resolved = CHECKS if checks is None else checks
    return [drift for check in resolved for drift in check(facts)]


def _side(file: str, line: int | None) -> str:
    return f"{file}:{line}" if line is not None else file


def render(drifts: list[Drift], checks_run: int) -> str:
    if checks_run == 0:
        return "no checks registered: nothing measured"
    if not drifts:
        return f"no drift ({checks_run} checks)"
    return "\n".join(
        f"{d.check}: {_side(d.a_file, d.a_line)} <-> {_side(d.b_file, d.b_line)} — {d.correction}"
        for d in drifts
    )


def to_json(drifts: list[Drift]) -> list[dict]:
    return [dataclasses.asdict(d) for d in drifts]


def facts_plan(root: str, manifest: Mapping) -> dict:
    components = manifest.get("components", {})
    version = manifest.get("coxswain", {}).get("version")
    umbrella = str(Path(root) / "coxswain")
    return {
        "root": root,
        "umbrella": umbrella,
        "cli_docs_dir": str(Path(umbrella) / "docs" / "reference" / "cli"),
        "component_dirs": {name: release.component_dir(root, name) for name in components},
        "component_docs": {name: str(Path(umbrella) / "docs" / "components" / f"{name}.md") for name in components},
        "release_notes": str(Path(umbrella) / "docs" / "releases" / f"{version}.md") if version else None,
        "readmes": {name: str(Path(release.component_dir(root, name)) / "README.md") for name in components},
        "pyprojects": {name: str(Path(release.component_dir(root, name)) / "pyproject.toml") for name in components},
    }
