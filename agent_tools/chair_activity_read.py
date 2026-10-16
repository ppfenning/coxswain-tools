"""Edge readers for the chair's session and agent transcripts and the price catalog. Every directory is an argument."""

from __future__ import annotations

import glob
import json
from collections.abc import Callable, Mapping
from pathlib import Path

import yaml

from agent_tools.stats_chair import catalog_prices


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError, UnicodeDecodeError:
        return []


def _parse_meta(text: str | None) -> dict | None:
    try:
        meta = json.loads(text) if text is not None else None
    except ValueError:
        return None
    return meta if isinstance(meta, dict) else None


def read_agents(projects_dir: Path, session: str) -> list[dict]:
    """Agents of `session` sorted by agent_id; an unreadable or non-JSON meta file is skipped."""
    if not session:
        return []
    pattern = f"*/{glob.escape(session)}/subagents/agent-*.meta.json"
    found = (
        (path.name.removeprefix("agent-").removesuffix(".meta.json"), path) for path in Path(projects_dir).glob(pattern)
    )
    agents = (
        {
            "agent_id": agent_id,
            "meta": _parse_meta(_read_text(path)),
            "lines": _read_lines(path.with_name(f"agent-{agent_id}.jsonl")),
        }
        for agent_id, path in found
    )
    return sorted((a for a in agents if a["meta"] is not None), key=lambda a: a["agent_id"])


def read_session_lines(projects_dir: Path, session: str) -> list[str]:
    """Lines of the first `*/<session>.jsonl`; empty when none exists or `session` is empty."""
    if not session:
        return []
    matches = sorted(Path(projects_dir).glob(f"*/{glob.escape(session)}.jsonl"))
    return _read_lines(matches[0]) if matches else []


def _catalog_candidates(profile: Mapping) -> list[Path]:
    """Catalog locations in priority order: beside the provider profile, then under the cartridges dir."""
    beside = [Path(profile["provider_profile"]).parent / "catalog.yaml"] if profile.get("provider_profile") else []
    under = [Path(profile["cartridges_dir"]) / "providers" / "catalog.yaml"] if profile.get("cartridges_dir") else []
    return [*beside, *under]


def prices_for_profile(
    profile: Mapping | None,
    read_text: Callable[[Path], str | None] | None = None,
) -> dict[str, dict[str, float]]:
    """model -> price from the first readable catalog.yaml for `profile`; {} when none is readable or the YAML is invalid."""
    if not profile:
        return {}
    reader = read_text or _read_text
    texts = (text for text in map(reader, _catalog_candidates(profile)) if text is not None)
    first = next(texts, None)
    if first is None:
        return {}
    try:
        return catalog_prices(yaml.safe_load(first))
    except yaml.YAMLError:
        return {}
