"""check_notes: every release-notes bullet must name a component and cite a
landed PR or commit in that component's history."""

from __future__ import annotations

import re
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
    component = next((name for name in sorted(components)
                       if re.search(rf"\b{re.escape(name)}\b(?!://)", text)), None)
    citations = set(_PR.findall(text)) | set(_SHA.findall(text))
    return component, citations


def _resolves(citation: str, known: set[str]) -> bool:
    if citation in known:
        return True
    return len(citation) >= 7 and any(len(k) >= 7 and (k.startswith(citation) or citation.startswith(k)) for k in known)


def bullets_from_notes(text: str) -> list[tuple[int, str]]:
    """Each bullet with its continuation lines joined. The notes wrap at 100
    columns, so a bullet's citation usually sits on its second or third line;
    a continuation is an indented, non-blank, non-heading line that directly
    follows the bullet or another continuation. A blank line or a heading ends
    the bullet, so a later indented paragraph is never glued onto it."""
    bullets: list[tuple[int, str]] = []
    open_bullet = False
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if _BULLET.match(line):
            bullets.append((i, line))
            open_bullet = True
        elif open_bullet and line and raw[:1].isspace() and not line.startswith("#"):
            n, so_far = bullets[-1]
            bullets[-1] = (n, f"{so_far} {line}")
        else:
            open_bullet = False
    return bullets


def landed_from_git(text: str) -> set[str]:
    return {line.split()[0] for line in text.splitlines() if line.strip()}


def previous_version(version: str, versions: list[str]) -> str | None:
    def key(v: str) -> tuple[int, ...]:
        return tuple(map(int, re.findall(r"\d+", v)))
    return max((v for v in versions if key(v) < key(version)), key=key, default=None)


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
    landed: Mapping[str, set[str]] = facts.get("landed", {})
    components: set[str] = set(component_dirs) | set(landed)
    measured: Mapping[str, bool] = facts.get("pr_numbers_measured", {})
    notes_path = facts.get("release_notes", "")
    drifts = [
        _bullet_drift(notes_path, line_no, text, components, landed, component_dirs, measured.get(parse_bullet(text, components)[0], True))
        for line_no, text in facts.get("notes_bullets", [])
    ]
    return [d for d in drifts if d is not None]


def _component_landed(directory: str, run: Callable, version: str, previous: str | None) -> set[str]:
    if not Path(directory).is_dir():
        return set()

    def has_tag(tag: str) -> bool:
        return bool(run(["git", "rev-parse", "--verify", "--quiet", tag], cwd=directory, capture_output=True, text=True).stdout.strip())

    rev = (f"v{previous}.." if previous and has_tag(f"v{previous}") else "") + (f"v{version}" if has_tag(f"v{version}") else "HEAD")
    log = run(["git", "log", "--oneline", rev], cwd=directory, capture_output=True, text=True).stdout
    # PR numbers come from the range's own subjects: `gh pr list` returns only 30 PRs unless --limit is passed,
    # and it also lists PRs merged after the version's tag.
    return landed_from_git(log) | set(_PR.findall(log))


def gather_notes_facts(root: str, manifest: Mapping, run: Callable) -> dict:
    components = manifest.get("components", {})
    version = manifest.get("coxswain", {}).get("version")
    notes_path = Path(root) / "coxswain" / "docs" / "releases" / f"{version}.md" if version else None
    umbrella_dir = str(Path(root) / "coxswain")
    stems = [p.stem for p in (Path(umbrella_dir) / "docs" / "releases").glob("*.md") if re.fullmatch(r"\d+(\.\d+)*", p.stem)]
    previous = previous_version(version, stems) if version else None

    def landed(directory: str) -> set[str]:
        return _component_landed(directory, run, version or "", previous)

    return {
        "notes_bullets": bullets_from_notes(notes_path.read_text()) if notes_path and notes_path.exists() else [],
        "landed": {name: landed(release.component_dir(root, name)) for name in components}
        | {"coxswain": landed(umbrella_dir)},
    }
