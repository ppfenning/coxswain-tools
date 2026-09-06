"""Rows for the cast roster: one per seat in the resolved cast, and the
three edits a fragment write can make to a seat's entry.

Pure: plain mappings in, plain dicts out. No filesystem, no clock.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Row:
    seat: str
    enabled: bool
    layer: str
    skills_count: int
    context: str


def _entry(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _layer_of(layers: Sequence[tuple[str, Mapping]], seat: str) -> str:
    owners = [name for name, cast in layers if seat in _entry(cast)]
    return owners[-1] if owners else "unknown"


def rows(resolved_cast: Mapping, layers: Sequence[tuple[str, Mapping]]) -> tuple[Row, ...]:
    return tuple(
        Row(
            seat=seat,
            enabled=bool(_entry(value).get("enabled", True)),
            layer=_layer_of(layers, seat),
            skills_count=len(_entry(value).get("skills") or []),
            context=_entry(value).get("context", ""),
        )
        for seat, value in resolved_cast.items()
    )


def toggle(fragment: Mapping, seat: str) -> dict:
    """Flips `seat`'s enabled flag as `fragment` (or the resolved cast passed
    in its place) currently has it, defaulting a seat with no flag to enabled."""
    current = _entry(_entry(fragment.get("cast")).get(seat)).get("enabled", True)
    return {"cast": {seat: {"enabled": not current}}}


def set_skills(seat: str, skills: Sequence[str]) -> dict:
    return {"cast": {seat: {"skills": list(skills)}}}


def set_context(seat: str, context: str) -> dict:
    return {"cast": {seat: {"context": context}}}
