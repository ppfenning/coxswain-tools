"""check_notes: every release-notes bullet must name a component and cite a
landed PR or commit in that component's history."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from agent_tools import release

_PR = re.compile(r"#(\d+)")
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_BULLET = re.compile(r"^[-*]\s+\S")


if TYPE_CHECKING:
    from agent_tools.release_check import Drift

def parse_bullet(text: str, components: set[str]) -> tuple[str | None, set[str]]:
    component = next((name for name in sorted(components) if re.search(rf"\b{re.escape(name)}\b", text)), None)
    citations = set(_PR.findall(text)) | set(_SHA.findall(text))
    return component, citations


def _resolves(citation: str, known: set[str]) -> bool:
    if citation in known:
        return True
    return len(citation) >= 7 and any(len(k) >= 7 and (k.startswith(citation) or citation.startswith(k)) for k in known)


def bullets_from_notes(text: str) -> list[tuple[int, str]]:
    return [(i, line.strip()) for i, line in enumerate(text.splitlines(), start=1) if _BULLET.match(line.strip())]


def landed_from_git(text: str) -> set[str]:
    return {line.split()[0] for line in text.splitlines() if line.strip()}


def landed_from_gh(text: str) -> set[str]:
    try:
        prs = json.loads(text)
    except json.JSONDecodeError:
        return set()
    if not isinstance(prs, list):
        return set()
    return {str(pr["number"]) for pr in prs if isinstance(pr, dict) and "number" in pr}


def _bullet_drift(notes_path: str, line_no: int, text: str, components: set[str],
                   landed: Mapping[str, set[str]], component_dirs: Mapping[str, str], pr_numbers_measured: bool = True) -> Drift | None:
    from agent_tools.release_check import Drift

    component, citations = parse_bullet(text, components)
    if component is None:
        return Drift("notes_citation", notes_path, line_no, notes_path, None,
                      "name a landed component for this bullet")
    known = landed.get(component, set())
    if not citations:
        return Drift("notes_citation", notes_path, line_no, component_dirs.get(component, component), None,
                      f"cite the PR or commit landed in {component}")
    if not any(_resolves(c, known) for c in citations):
        if not pr_numbers_measured and any(c.isdigit() for c in citations):
            return None
        return Drift("notes_citation", notes_path, line_no, component_dirs.get(component, component), None,
                      f"cite a PR or commit landed in {component}, or remove")
    return None


def check_notes(facts: Mapping) -> list[Drift]:
    component_dirs: Mapping[str, str] = facts.get("component_dirs", {})
    components: set[str] = set(component_dirs)
    landed: Mapping[str, set[str]] = facts.get("landed", {})
    measured: Mapping[str, bool] = facts.get("pr_numbers_measured", {})
    notes_path = facts.get("release_notes", "")
    drifts = [
        _bullet_drift(notes_path, line_no, text, components, landed, component_dirs, measured.get(parse_bullet(text, components)[0], True))
        for line_no, text in facts.get("notes_bullets", [])
    ]
    return [d for d in drifts if d is not None]


def _component_landed(directory: str, run: Callable, gh_available: bool) -> set[str]:
    if not Path(directory).is_dir():
        return set()
    git_result = run(["git", "log", "--oneline"], cwd=directory, capture_output=True, text=True)
    found = landed_from_git(git_result.stdout)
    if not gh_available:
        return found
    gh_result = run(["gh", "pr", "list", "--state", "merged", "--json", "number"],
                     cwd=directory, capture_output=True, text=True)
    return found | landed_from_gh(gh_result.stdout)


def gather_notes_facts(root: str, manifest: Mapping, run: Callable) -> dict:
    components = manifest.get("components", {})
    version = manifest.get("coxswain", {}).get("version")
    notes_path = Path(root) / "coxswain" / "docs" / "releases" / f"{version}.md" if version else None
    gh_available = shutil.which("gh") is not None
    return {
        "notes_bullets": bullets_from_notes(notes_path.read_text()) if notes_path and notes_path.exists() else [],
        "landed": {name: _component_landed(release.component_dir(root, name), run, gh_available) for name in components},
        "pr_numbers_measured": dict.fromkeys(components, gh_available),
    }
