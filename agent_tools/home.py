"""Location resolver for the coxswain home, per docs/design/coxswain-home.md.

Field to design: home is the data directory (question 2), installs and
workspaces are its two folders, config is always the tilde config folder,
state is the XDG state subfolder, profile and record sit under config.

Rules: the first set, non-empty home source wins (question 5). An empty
value counts as unset. A relative flag or COXSWAIN_HOME is Refused. A
relative XDG value or relative record counts as unset.
"""

from __future__ import annotations

import os
import posixpath
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Locations:
    home: str
    installs: str
    workspaces: str
    config: str
    state: str
    profile: str
    record: str


@dataclass(frozen=True)
class Refused:
    reason: str


def _absolute(raw: str | None) -> str | None:
    """`raw` when it is non-empty and absolute, else None."""
    return raw if raw and posixpath.isabs(raw) else None


def _xdg(env: Mapping[str, str], name: str, fallback: str) -> str:
    base = _absolute(env.get(name))
    return posixpath.join(base, "coxswain") if base else fallback


def resolve(
    env: Mapping[str, str],
    user_home: str,
    platform: str,
    flag: str | None = None,
    record: str | None = None,
) -> Locations | Refused:
    """Resolve every location. `platform` is accepted and changes nothing: the design has no per-platform rule."""
    for label, raw in (("--home", flag), ("COXSWAIN_HOME", env.get("COXSWAIN_HOME"))):
        if raw and not posixpath.isabs(raw):
            return Refused(f"{label} must be an absolute path, got {raw!r}")
    default_home = _xdg(env, "XDG_DATA_HOME", posixpath.join(user_home, ".local", "share", "coxswain"))
    home = flag or env.get("COXSWAIN_HOME") or _absolute(record and record.strip()) or default_home
    config = posixpath.join(user_home, ".config")
    return Locations(
        home=home,
        installs=posixpath.join(home, "installs"),
        workspaces=posixpath.join(home, "workspaces"),
        config=config,
        state=_xdg(env, "XDG_STATE_HOME", posixpath.join(user_home, ".local", "state", "coxswain")),
        profile=env.get("AGENT_TOOLS_PROFILE") or posixpath.join(config, "agent-tools", "profile.yaml"),
        record=posixpath.join(config, "agent-tools", "home"),
    )


def _read_record(path: str) -> str | None:
    try:
        return Path(path).read_text().strip() or None
    except OSError:
        return None


def resolve_here(flag: str | None = None) -> Locations | Refused:
    """Edge. Read the environment, the home directory and the home record, then resolve."""
    user_home = str(Path.home())
    record_path = posixpath.join(user_home, ".config", "agent-tools", "home")
    return resolve(os.environ, user_home, sys.platform, flag, _read_record(record_path))


def write_home_record(locations: Locations) -> None:
    """Edge. Write the resolved home as the one-line home record."""
    path = Path(locations.record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(locations.home + "\n")
