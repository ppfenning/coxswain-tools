"""Pure lookup of a standing squash conflict, keyed by (initiative, phase, run)."""

from collections.abc import Iterable
from typing import TypedDict


class LandConflict(TypedDict):
    initiative: str
    phase: str
    run: str
    paths: list[str]


def standing(conflicts: Iterable[LandConflict], initiative: str, phase: str, run: str) -> LandConflict | None:
    """The conflict recorded for this exact run; a new run id of the phase matches none."""
    return next(
        (c for c in conflicts if (c.get("initiative"), c.get("phase"), c.get("run")) == (initiative, phase, run)),
        None,
    )
