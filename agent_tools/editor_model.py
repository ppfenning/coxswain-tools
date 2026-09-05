"""Rows for the cartridge editor: what the probe resolved, and whether it can
be written back, decided by which layer's provenance owns it.

Pure: takes the probe dict the doctor's real-cartridge probe already
produces, returns plain data. No filesystem, no clock.
"""

from __future__ import annotations

import functools
import itertools
from dataclasses import dataclass, replace
from typing import Any

from agent_tools.provenance import _FIXED_PATHS

_MISSING = object()

_CHOICE_VALUES: dict[str, tuple[str, ...]] = {
    "policy.review_tier": ("1", "2", "3"),
    "policy.plan_competition.min_tier": ("1", "2", "3"),
}


@dataclass(frozen=True)
class Row:
    key: str
    value: Any
    layer: str
    editable: bool
    kind: str
    choices: tuple[Any, ...] = ()


def _step(node, part):
    if isinstance(node, dict) and part in node:
        return node[part]
    if part == "crew" and isinstance(node, dict) and "cast" in node:
        return node["cast"]
    return _MISSING


def _get(value: dict, path: str) -> Any:
    return functools.reduce(_step, path.split("."), value)


def _crew(resolved: dict) -> dict:
    if not isinstance(resolved, dict):
        return {}
    return resolved["crew"] or {} if "crew" in resolved else (resolved.get("cast") or {})


def _section(key: str) -> str:
    return key.split(".", 1)[0]


def _kind(key: str) -> str:
    if key.endswith(".enabled"):
        return "toggle"
    if key in _CHOICE_VALUES:
        return "choice"
    return "text"


def _choices(key: str) -> tuple[Any, ...]:
    return _CHOICE_VALUES.get(key, ())


def _seat_field_paths(resolved: dict, seat: str, value) -> list[str]:
    if not isinstance(value, dict):
        return [f"crew.{seat}"]
    return [
        f"crew.{seat}.{field}"
        for field in ("enabled", "skills")
        if _get(resolved, f"crew.{seat}.{field}") is not _MISSING
    ]


def _keys(resolved: dict) -> list[str]:
    seats = [path for seat, value in _crew(resolved).items() for path in _seat_field_paths(resolved, seat, value)]
    skills_map = resolved.get("skills") if isinstance(resolved, dict) else None
    skills = [
        f"skills.{key}"
        for key in (skills_map if isinstance(skills_map, dict) else {})
        if _get(resolved, f"skills.{key}") is not _MISSING
    ]
    fixed = [key for key in _FIXED_PATHS if _get(resolved, key) is not _MISSING]
    return seats + skills + fixed


def rows(probe: dict, team: str) -> list[Row]:
    resolved = probe.get("resolved") or {}
    provenance = probe.get("provenance") or {}
    broken = "provenance_error" in probe
    return sorted(
        (
            Row(
                key=key,
                value=_get(resolved, key),
                layer="unknown" if broken else provenance.get(key, "unknown"),
                editable=(not broken) and provenance.get(key) in (team, "edited"),
                kind=_kind(key),
                choices=_choices(key),
            )
            for key in _keys(resolved)
        ),
        key=lambda row: (_section(row.key), row.key),
    )


def sections(items: list[Row]) -> dict[str, list[Row]]:
    """Group rows by their leading key segment. `rows()` already sorts by
    section then key; re-sort defensively so a caller need not."""
    ordered = sorted(items, key=lambda row: _section(row.key))
    return {
        section: list(group)
        for section, group in itertools.groupby(ordered, key=lambda row: _section(row.key))
    }


@dataclass(frozen=True)
class State:
    rows: tuple[Row, ...]
    cursor: int
    pending: dict[str, object]
    message: str
    editing: str | None = None  # the key `e` selected; apply_text writes there, not at the cursor


def _editable_indexes(rows: tuple[Row, ...]) -> list[int]:
    return [i for i, row in enumerate(rows) if row.editable]


def _current_row(state: State) -> Row | None:
    if 0 <= state.cursor < len(state.rows):
        return state.rows[state.cursor]
    return None


def _move(state: State, delta: int) -> State:
    indexes = _editable_indexes(state.rows)
    if not indexes:
        return replace(state, message="no editable rows")
    if delta > 0:
        ahead = [i for i in indexes if i > state.cursor]
        target = ahead[0] if ahead else indexes[-1]
    else:
        behind = [i for i in indexes if i < state.cursor]
        target = behind[-1] if behind else indexes[0]
    return replace(state, cursor=target, message="")


def _without(pending: dict[str, object], key: str) -> dict[str, object]:
    return {k: v for k, v in pending.items() if k != key}


def _coerce_like(current: object, candidate: str) -> object:
    """`candidate` in `current`'s type when that conversion is meaningful, else the text itself."""
    try:
        return type(current)(candidate)
    except (TypeError, ValueError):
        return candidate


def _space(state: State) -> State:
    row = _current_row(state)
    if row is None:
        return replace(state, message="no row selected")
    if not row.editable:
        return replace(state, message=f"{row.key} is read-only")
    if row.kind == "toggle":
        if row.key in state.pending:
            return replace(state, pending=_without(state.pending, row.key), message=f"{row.key} reverted")
        new_value = not row.value
        return replace(state, pending={**state.pending, row.key: new_value}, message=f"{row.key} -> {new_value}")
    if row.kind == "choice":
        if not row.choices:
            return replace(state, message=f"{row.key} has no choices to cycle")
        current = state.pending.get(row.key, row.value)
        # a value outside the row's own choices (e.g. from an unrecognised layer)
        # is treated as coming before the first choice, so the first press lands on it
        at = row.choices.index(str(current)) if str(current) in row.choices else -1
        candidate = row.choices[(at + 1) % len(row.choices)]
        # errors are values: a None or oddly typed current value takes the choice as text
        new_value = candidate if current is None or isinstance(current, str) else _coerce_like(current, candidate)
        if new_value == row.value:
            return replace(state, pending=_without(state.pending, row.key), message=f"{row.key} reverted")
        return replace(state, pending={**state.pending, row.key: new_value}, message=f"{row.key} -> {new_value}")
    return replace(state, message=f"{row.key} has nothing to toggle")


def _edit(state: State) -> State:
    row = _current_row(state)
    if row is None:
        return replace(state, message="no row selected")
    if not row.editable or row.kind != "text":
        return replace(state, message=f"{row.key} cannot be edited")
    return replace(state, editing=row.key, message=f"editing {row.key}")


def apply_text(state: State, text: str) -> State:
    """The edge calls this once it has the text `e` asked for. The target is the
    row `e` selected (`state.editing`), never the cursor, so keys pressed between
    the prompt and the reply cannot redirect the text."""
    key = state.editing
    if key is None:
        return replace(state, message="nothing is being edited; press e on a text row first")
    return replace(state, pending={**state.pending, key: text}, editing=None, message=f"{key} set")


def _undo(state: State) -> State:
    row = _current_row(state)
    if row is None:
        return replace(state, message="no row selected")
    if row.key not in state.pending:
        return replace(state, message="")
    return replace(state, pending=_without(state.pending, row.key), message=f"{row.key} reverted")


_HANDLERS = {
    "j": lambda s: _move(s, 1),
    "k": lambda s: _move(s, -1),
    "space": _space,
    "e": _edit,
    "u": _undo,
    "w": lambda s: replace(s),  # piece 3 turns pending into an effect here
    "q": lambda s: replace(s),
}


def step(state: State, key: str) -> State:
    handler = _HANDLERS.get(key, lambda s: replace(s, message=f"unknown key {key}"))
    return handler(state)
