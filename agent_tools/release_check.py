"""The pure core of `cox dev release-check`: drifts computed from facts the
edge gathers. No check here reads a file, runs a command or touches the
network — `cli.py` gathers the facts named by `facts_plan` and calls in."""

from __future__ import annotations

import dataclasses
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

from agent_tools import release, release_check_cli, release_check_notes
from agent_tools.release_check_manifest import check_manifest
from agent_tools.release_check_pages import check_pages
from agent_tools.release_check_readmes import check_readmes


@dataclasses.dataclass(frozen=True)
class Drift:
    check: str
    a_file: str
    a_line: int | None
    b_file: str
    b_line: int | None
    correction: str


def check_versions(facts: Mapping) -> list[Drift]:
    """Every component pyproject and the umbrella's must equal `expected_version`
    (the manifest's `coxswain.version`, gathered by `gather_version_facts`)."""
    expected = facts.get("expected_version")
    if expected is None:
        return []
    manifest_file = facts.get("manifest_path", "manifest.toml")

    def mismatch(label: str, pyproject_file: str, found: str | None) -> Drift | None:
        if found is None or found == expected:
            return None
        return Drift("versions", manifest_file, None, pyproject_file, None,
                     f"{label} pyproject.toml is {found}, manifest wants {expected} "
                     f"(cox dev release {expected} performs the bump)")

    umbrella_file = str(Path(facts.get("umbrella", "coxswain")) / "pyproject.toml")
    umbrella_found = facts.get("umbrella_pyproject", {}).get("project", {}).get("version")
    umbrella_drift = mismatch("umbrella", umbrella_file, umbrella_found)

    pyprojects = facts.get("pyprojects", {})
    component_drifts = [
        d
        for name, pyproject in facts.get("component_pyprojects", {}).items()
        for d in [mismatch(name, pyprojects.get(name, f"{name}/pyproject.toml"), (pyproject or {}).get("project", {}).get("version"))]
        if d is not None
    ]
    return ([umbrella_drift] if umbrella_drift is not None else []) + component_drifts


def gather_version_facts(manifest: Mapping, manifest_path: str, component_dirs: Mapping[str, str], umbrella: str) -> dict:
    """Edge for `check_versions`: the manifest's declared version, and each
    component's and the umbrella's pyproject.toml read off disk (`{}` when absent)."""

    def read(path: Path) -> dict:
        return tomllib.loads(path.read_text()) if path.exists() else {}

    return {
        "expected_version": manifest.get("coxswain", {}).get("version"),
        "manifest_path": manifest_path,
        "component_pyprojects": {name: read(Path(d) / "pyproject.toml") for name, d in component_dirs.items()},
        "umbrella_pyproject": read(Path(umbrella) / "pyproject.toml"),
    }


CHECKS: tuple[Callable[[Mapping], list[Drift]], ...] = (release_check_cli.check_cli_surface, check_manifest, release_check_notes.check_notes, check_pages, check_readmes, check_versions)


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
