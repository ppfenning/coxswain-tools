"""Deprecations whose removal condition is data: a calendar date or a named check.

Pure: the caller passes today's date and the check results. `evaluate` returns an
`Evaluation` with `met` (condition holds) and `unknown` (check name absent from the mapping).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Deprecation:
    """Exactly one of `on_date` and `check` is set; build it with `make_deprecation`."""

    name: str
    message: str
    owner: str
    on_date: date | None = None
    check: str | None = None


@dataclass(frozen=True)
class DeprecationError:
    reason: str


@dataclass(frozen=True)
class Evaluation:
    met: tuple[Deprecation, ...]
    unknown: tuple[Deprecation, ...]


def make_deprecation(
    name: str,
    message: str,
    owner: str,
    on_date: date | None = None,
    check: str | None = None,
) -> Deprecation | DeprecationError:
    if on_date is not None and check is not None:
        return DeprecationError(f"{name}: condition holds both a date and a check")
    if on_date is None and check is None:
        return DeprecationError(f"{name}: condition holds neither a date nor a check")
    return Deprecation(name, message, owner, on_date, check)


def is_met(dep: Deprecation, today: date, checks: Mapping[str, bool]) -> bool | None:
    """True or False when decidable; None when the named check has no result."""
    if dep.on_date is not None:
        return today >= dep.on_date
    if dep.check is not None and dep.check in checks:
        return checks[dep.check]
    return None


def evaluate(deprecations: tuple[Deprecation, ...], today: date, checks: Mapping[str, bool]) -> Evaluation:
    results = tuple((dep, is_met(dep, today, checks)) for dep in deprecations)
    return Evaluation(
        met=tuple(dep for dep, result in results if result is True),
        unknown=tuple(dep for dep, result in results if result is None),
    )
