"""Readers for the chair's `intake` and `sources_configured` facts.

The core is pure. The edge lists intake with `route.intake_entries` and reads the profile with `route.parse_profile`.
"""
from collections.abc import Mapping, Sequence
from pathlib import Path

from agent_tools import route


def undecomposed_oldest_first(entries: Sequence[tuple[Mapping, float]]) -> list[str]:
    """Paths of `route.intake_entries` rows not `done`, oldest mtime first; equal mtimes fall back to path."""
    pending = [(mtime, row["path"]) for row, mtime in entries if not row["done"]]
    return [path for _, path in sorted(pending)]


def has_sources(sources: Sequence[str]) -> bool:
    return len(sources) > 0


def read_intake(ws: Path) -> list[str]:
    """Edge. Undecomposed intake paths under `ws/intake`, oldest first; none when the directory is absent."""
    root = ws / "intake"
    paths = sorted(root.glob("*.md")) + sorted((root / "done").glob("*.md"))
    files = {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in paths}
    mtimes = {name: (root / name).stat().st_mtime for name in files}
    return undecomposed_oldest_first([(row, mtimes[_filename(row)]) for row in route.intake_entries(files)])


def _filename(row: Mapping) -> str:
    """Relative name a row was listed under: `intake/x.md` is `x.md`, and a done row lives under `done/`."""
    name = row["path"].removeprefix("intake/")
    return f"done/{name}" if row["done"] else name


def read_sources_configured(profile_path: Path) -> bool:
    """Edge. True when the profile's `sources` block names any source; an unreadable profile configures none."""
    try:
        text = profile_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return has_sources(list(route.parse_profile(text).get("sources", {})))
