"""The manifest check: the umbrella manifest against the docs pages and release notes for the cut version."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_tools.release_check import Drift

_VERSION_RE = re.compile(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")


def versions_in(text: str) -> set[str]:
    return set(_VERSION_RE.findall(text))


def check_manifest(facts: Mapping) -> list[Drift]:
    from agent_tools.release_check import Drift

    manifest = facts.get("manifest", {})
    components = manifest.get("components", {})
    version = manifest.get("coxswain", {}).get("version")
    manifest_file = facts.get("manifest_path", "manifest.toml")
    component_docs = facts.get("component_docs", {})
    pages = facts.get("component_pages", {})
    notes_file = facts.get("release_notes") or (f"docs/releases/{version}.md" if version else "docs/releases/<version>.md")
    notes_page = facts.get("notes_page")

    def page_drift(name: str, spec: Mapping) -> Drift | None:
        page_file = component_docs.get(name, f"docs/components/{name}.md")
        page = pages.get(name)
        target_version = str(spec.get("tag") or "").lstrip("v") or version
        if page is None:
            return Drift("manifest", manifest_file, None, page_file, None, f"add {page_file} for {name}")
        if target_version and f"v{target_version}" not in versions_in(page):
            stale_line = next((i + 1 for i, line in enumerate(page.splitlines()) if versions_in(line)), None)
            return Drift("manifest", manifest_file, None, page_file, stale_line, f"update {page_file} to v{target_version}")
        return None

    page_drifts = [d for name, spec in components.items() for d in [page_drift(name, spec or {})] if d is not None]
    if notes_page is None:
        notes_drifts = [Drift("manifest", manifest_file, None, notes_file, None, f"add {notes_file}")] if components else []
    else:
        notes_drifts = [
            Drift("manifest", manifest_file, None, notes_file, None, f"mention {name} in {notes_file}")
            for name in components
            if not re.search(rf"\b{re.escape(name)}\b", notes_page)
        ]
    return page_drifts + notes_drifts


def gather_manifest_facts(
    manifest: Mapping, manifest_path: str, component_docs: Mapping[str, str], release_notes: str | None
) -> dict:
    component_pages = {name: Path(path).read_text() for name, path in component_docs.items() if Path(path).exists()}
    notes_page = Path(release_notes).read_text() if release_notes and Path(release_notes).exists() else None
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "component_pages": component_pages,
        "notes_page": notes_page,
    }
