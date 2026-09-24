"""Pure layout for `cox home`: which panels show, in what order, at what height, and the keys that change it."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from agent_tools import home_model, panel, regatta
from agent_tools.home_model import Line

__all__ = ["DEFAULT", "KEYS", "PANELS", "Layout", "focus_after", "from_json", "rects", "render", "step", "to_json"]

PANELS = ("regatta", "leader", "backlog", "window", "runs")
KEYS = ("1", "2", "3", "4", "5", "<", ">", "+", "-")
MIN_HEIGHT = 3
WEIGHT_STEP = 0.05
WEIGHT_MIN = 0.15
WEIGHT_MAX = 0.7

_TITLES = {"regatta": "REGATTA", "leader": "Leader", "backlog": "Backlog", "window": "Window", "runs": "Runs"}
_MOVES = {"<": -1, ">": 1}
_WEIGHT_DELTAS = {"+": WEIGHT_STEP, "-": -WEIGHT_STEP}


@dataclass(frozen=True)
class Layout:
    order: tuple[str, ...]
    hidden: frozenset[str]
    runs_weight: float


DEFAULT = Layout(PANELS, frozenset(), 0.4)


def _heights(shown: tuple[str, ...], runs_weight: float, height: int) -> tuple[int, ...]:
    """One height per shown panel summing to `height`; runs takes its weight, clamped so the others keep `MIN_HEIGHT`."""
    others = len(shown) - ("runs" in shown)
    runs = height if not others else min(max(round(runs_weight * height), MIN_HEIGHT), max(height - others * MIN_HEIGHT, 0))
    base, extra = divmod(height - (runs if "runs" in shown else 0), max(others, 1))
    spread = iter(base + (i < extra) for i in range(others))
    return tuple(runs if name == "runs" else next(spread) for name in shown)


def rects(layout: Layout, width: int, height: int) -> dict[str, tuple[int, int, int, int]]:
    """Name to (row, col, width, height) for each visible panel, stacked full width in order with no gap."""
    shown = tuple(name for name in layout.order if name not in layout.hidden)
    heights = _heights(shown, layout.runs_weight, height)
    rows = tuple(sum(heights[:i]) for i in range(len(shown)))
    return {name: (row, 0, width, h) for name, row, h in zip(shown, rows, heights)}


def step(layout: Layout, key: str, focus: str = "runs") -> Layout:
    """`1`-`5` toggle `PANELS[n-1]`; `<` `>` move `focus` one place; `+` `-` shift `runs_weight` within its bounds."""
    if key in KEYS[:5]:
        return dataclasses.replace(layout, hidden=layout.hidden ^ {PANELS[int(key) - 1]})
    if key in _MOVES and focus in layout.order:
        i = layout.order.index(focus)
        j = min(max(i + _MOVES[key], 0), len(layout.order) - 1)
        order = list(layout.order)
        order[i], order[j] = order[j], order[i]
        return dataclasses.replace(layout, order=tuple(order))
    if key in _WEIGHT_DELTAS:
        weight = round(layout.runs_weight + _WEIGHT_DELTAS[key], 2)
        return dataclasses.replace(layout, runs_weight=min(max(weight, WEIGHT_MIN), WEIGHT_MAX))
    return layout


def focus_after(key: str, focus: str) -> str:
    """A digit key focuses the panel it toggles; any other key leaves focus where it was."""
    return PANELS[int(key) - 1] if key in KEYS[:5] else focus


def to_json(layout: Layout) -> dict:
    return {"order": list(layout.order), "hidden": sorted(layout.hidden), "runs_weight": layout.runs_weight}


def from_json(data: object) -> Layout:
    """`DEFAULT` for anything that is not a complete, in-range layout."""
    try:
        order, hidden, weight = tuple(data["order"]), frozenset(data["hidden"]), float(data["runs_weight"])
    except (KeyError, TypeError, ValueError):
        return DEFAULT
    valid = sorted(order) == sorted(PANELS) and hidden <= set(PANELS) and WEIGHT_MIN <= weight <= WEIGHT_MAX
    return Layout(order, hidden, weight) if valid else DEFAULT


def render(layout: Layout, facts: home_model.Facts, width: int, height: int) -> tuple[Line, ...]:
    bodies = {
        "regatta": lambda w: regatta.regatta(facts.runs_rows, w, facts.tick),
        "leader": lambda w: home_model.chair_pane(facts, w),
        "backlog": lambda w: home_model.backlog_pane(facts, w),
        "window": lambda w: home_model.window_pane(facts, w),
        "runs": lambda w: home_model.runs_pane(facts, w),
    }
    return tuple(
        line
        for name, (_, _, w, h) in rects(layout, width, height).items()
        for line in panel.box(_TITLES[name], bodies[name](w - 2), w, h)
    )
