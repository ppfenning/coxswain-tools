"""The systemd user unit that keeps `cox chair run` alive. Pure text; the edge in cli.py does the file and PATH work."""

from __future__ import annotations

import posixpath
import re
from itertools import pairwise
from pathlib import Path

UNIT_NAME = "coxswain-chair.service"

# systemd splits ExecStart on whitespace and expands `%`, `$` and `\`; a value outside this set reaches the chair changed.
_UNIT_SAFE = re.compile(r"[A-Za-z0-9._@+:,/=-]+")
_EXEC_START = re.compile(r"^ExecStart=(.*)$", re.MULTILINE)
_ENV_FILE = re.compile(r"^EnvironmentFile=-(\S+)\s*$", re.MULTILINE)


def unit_path(home: Path) -> Path:
    return home / ".config/systemd/user" / UNIT_NAME


def systemctl_lines() -> list[str]:
    return [
        "systemctl --user daemon-reload",
        f"systemctl --user enable --now {UNIT_NAME}",
        f"systemctl --user status {UNIT_NAME}",
    ]


def unsafe_values(values: dict[str, str]) -> list[str]:
    """The names whose value systemd would split or expand, in the order given."""
    return [name for name, value in values.items() if not _UNIT_SAFE.fullmatch(value)]


def export_lines(env_text: str) -> list[int]:
    """1-based numbers of `export NAME=value` lines; systemd's EnvironmentFile= does not document that prefix."""
    return [n for n, line in enumerate(env_text.splitlines(), start=1) if line.lstrip().startswith("export ")]


def path_value(cox: str, tools_bin: str, graphs_bin: str, default_path: str) -> str:
    """The unit's PATH: cox's directory, then the two venv bins, then the default; empties dropped, first duplicate kept."""
    entries = [posixpath.dirname(cox), tools_bin, graphs_bin, *default_path.split(":")]
    return ":".join(dict.fromkeys(entry for entry in entries if entry))


def unit_text(
    cox: str, workspace_dir: str, label: str, interval: int, environment_file: str | None, profile: str | None = None,
    path: str | None = None,
) -> str:
    """`EnvironmentFile=-` keeps a missing file from failing the unit; `path` becomes the one `Environment=PATH=` line."""
    env_lines = [f"EnvironmentFile=-{environment_file}"] if environment_file is not None else []
    path_lines = [f"Environment=PATH={path}"] if path is not None else []
    profile_args = f" --profile {profile}" if profile is not None else ""
    lines = [
        "[Unit]",
        "Description=Coxswain autonomous chair",
        "",
        "[Service]",
        f"ExecStart={cox} chair run --label {label} --interval {interval}{profile_args}",
        f"WorkingDirectory={workspace_dir}",
        *path_lines,
        *env_lines,
        "Restart=on-failure",
        "RestartSec=30",
        "",
        "[Install]",
        "WantedBy=default.target",
    ]
    return "\n".join(lines) + "\n"


def _flag_value(tokens: list[str], flag: str) -> str | None:
    return next((value for name, value in pairwise(tokens) if name == flag), None)


def unit_args(text: str) -> dict[str, str | None] | None:
    """Reads label, interval, profile and env_file back out of a unit; an absent flag or line is None, interval stays a string."""
    commands = [match.group(1).split() for match in _EXEC_START.finditer(text)]
    tokens = next((t for t in commands if ("chair", "run") in pairwise(t)), None)
    if tokens is None:
        return None
    env = _ENV_FILE.search(text)
    return {
        "label": _flag_value(tokens, "--label"),
        "interval": _flag_value(tokens, "--interval"),
        "profile": _flag_value(tokens, "--profile"),
        "env_file": env.group(1) if env is not None else None,
    }
