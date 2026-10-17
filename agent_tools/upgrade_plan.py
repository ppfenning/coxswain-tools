"""Pure upgrade planner: what an upgrade must do and which components it leaves behind. No file, process, network or clock access."""
from collections.abc import Mapping
from dataclasses import dataclass

PIN_NOT_IN_REPO = "pin_not_in_repo"
NO_TAG_AT_TARGET = "no_tag_at_target"


@dataclass(frozen=True)
class Step:
    """One checkout move. `from_tag` is None when the component is not installed yet."""

    kind: str  # "umbrella" or "component"
    name: str
    from_tag: str | None
    to_tag: str


@dataclass(frozen=True)
class LeftBehind:
    name: str
    reason: str
    detail: str


@dataclass(frozen=True)
class Plan:
    """Umbrella step first, then component steps in name order."""

    steps: tuple[Step, ...]
    left_behind: tuple[LeftBehind, ...]


def release_tag(version: str) -> str:
    """A release tag always carries a leading v; a bare 0.43.0 becomes v0.43.0."""
    stripped = version.strip()
    return stripped if stripped.startswith("v") else f"v{stripped}"


def _decide(
    name: str, pin: str | None, installed: str | None, offered: frozenset[str]
) -> Step | LeftBehind | None:
    """None means the component is already at its pin."""
    if pin is None:
        return LeftBehind(name, NO_TAG_AT_TARGET, "the umbrella manifest carries no tag for it at the target release")
    if pin not in offered:
        return LeftBehind(name, PIN_NOT_IN_REPO, f"pinned tag {pin} is not offered by its repository")
    if installed == pin:
        return None
    return Step("component", name, installed, pin)


def plan_upgrade(
    target_tag: str,
    umbrella_tag: str,
    pins: Mapping[str, str | None],
    installed: Mapping[str, str | None],
    available: Mapping[str, frozenset[str]],
) -> Plan:
    """Umbrella checkout first, then one step per pinned component whose installed tag differs from its pin."""
    decisions = tuple(
        _decide(name, pins[name], installed.get(name), available.get(name, frozenset()))
        for name in sorted(pins)
    )
    return Plan(
        steps=(Step("umbrella", "umbrella", umbrella_tag, target_tag),)
        + tuple(d for d in decisions if isinstance(d, Step)),
        left_behind=tuple(d for d in decisions if isinstance(d, LeftBehind)),
    )
