"""check_release_index: a release's component-tag section must be verbatim in
docs/releases/index.md; shape (`## version`, one `- name: tag` line each) is
the ticket's own contract — the umbrella file is unreachable in this repo."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_tools.release_check import Drift


def index_section(version: str, component_tags: dict[str, str]) -> str:
    lines = "\n".join(f"- {name}: {tag}" for name, tag in sorted(component_tags.items()))
    return f"## {version}\n\n{lines}"


def check_release_index(facts: Mapping) -> list[Drift]:
    from agent_tools.release_check import Drift

    components = facts.get("manifest", {}).get("components", {})
    index_text = facts.get("releases_index", "")
    index_file = facts.get("releases_index_path", "docs/releases/index.md")
    return [
        Drift("release_index", index_file, None, index_file, None, f"add its section for {version}")
        for version in sorted(facts.get("release_versions", set()))
        for tags in [{
            name: f"v{version}" if spec.get("lockstep", True) else str(spec.get("tag"))
            for name, spec in components.items()
        }]
        if index_section(version, tags) not in index_text
    ]


def gather_release_index_facts(umbrella: str) -> dict:
    releases_dir = Path(umbrella) / "docs" / "releases"
    index_path = releases_dir / "index.md"
    versions = {p.stem for p in releases_dir.glob("*.md") if p.stem != "index"} if releases_dir.is_dir() else set()
    return {
        "release_versions": versions,
        "releases_index": index_path.read_text() if index_path.exists() else "",
        "releases_index_path": str(index_path),
    }
