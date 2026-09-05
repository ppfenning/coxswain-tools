"""Pure YAML fragment handling for the cartridge editor.

A fragment is the small, hand-edited YAML file that gets merged over
``cartridge.yaml``. Everything but ``write_fragment`` only ever works on
strings and plain data; ``write_fragment`` is the one edge, and it is the
only place in this module that touches a filesystem path. The merge rules
mirror the ones the cartridge loader documents: edits win, lists replace
wholesale, and the ``context`` key concatenates instead of replacing.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

HEADER = (
    "# written by the cartridge editor; edit freely, it is merged over\n"
    "# cartridge.yaml\n"
)


class FragmentError(Exception):
    """A fragment's top-level YAML document is not a mapping."""


def load_fragment(text: str) -> dict:
    parsed = yaml.safe_load(text)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise FragmentError(
            f"fragment top level must be a mapping, got {type(parsed).__name__}"
        )
    return parsed


def dump_fragment(data: dict) -> str:
    return HEADER + yaml.safe_dump(data, sort_keys=True)


def merge_edits(existing: dict, edits: dict) -> dict:
    merged = dict(existing)
    for key, value in edits.items():
        current = merged.get(key)
        if key == "context" and isinstance(current, list) and isinstance(value, list):
            merged[key] = current + value
        elif isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_edits(current, value)
        else:
            merged[key] = value
    return merged


def round_trips(text: str) -> bool:
    before = load_fragment(text)
    try:
        after = load_fragment(dump_fragment(before))
        return before == after
    except (yaml.YAMLError, RecursionError):
        # A self-referential anchor dumps and reloads as an equally
        # self-referential structure; comparing the two with `==` recurses
        # forever. Treat anything the guard cannot safely compare as a
        # fragment that does not round-trip.
        return False


def write_fragment(team_dir: Path, edits: dict) -> Path:
    fragment_dir = team_dir / "cartridge.d"
    path = fragment_dir / "edited.yaml"
    existing: dict = {}
    if path.exists():
        text = path.read_text()
        if not round_trips(text):
            raise FragmentError(
                "edited.yaml was not written by the editor or was hand-edited "
                "in a way that would not survive a rewrite; refusing to "
                "overwrite"
            )
        existing = load_fragment(text)
    merged = merge_edits(existing, edits)
    fragment_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = fragment_dir / "edited.yaml.tmp"
    tmp_path.write_text(dump_fragment(merged))
    os.replace(tmp_path, path)
    return path
