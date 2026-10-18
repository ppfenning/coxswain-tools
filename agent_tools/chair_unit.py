"""Effective PATH of the chair user unit, read across its unit file and drop-ins."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import reduce
from pathlib import Path

UNIT_NAME = "coxswain-chair.service"
DROP_IN_DIR = UNIT_NAME + ".d"

_PREFIX = "Environment="


@dataclass(frozen=True)
class PathObservation:
    path: str
    source: str


def _path_value(line: str) -> str | None:
    """PATH set by one line; one assignment per Environment= line, as the unit writes it."""
    stripped = line.strip()
    body = stripped[len(_PREFIX) :].strip() if stripped.startswith(_PREFIX) else ""
    unquoted = body[1:-1] if len(body) >= 2 and body[0] == body[-1] == '"' else body
    return unquoted[len("PATH=") :] if unquoted.startswith("PATH=") else None


def _path_in(text: str) -> str | None:
    values = tuple(v for v in map(_path_value, text.splitlines()) if v is not None)
    return values[-1] if values else None


def effective_path(unit_text: str | None, drop_ins: Sequence[tuple[str, str]]) -> PathObservation | None:
    """Unit file first, then drop-ins by file name; the last file setting PATH wins."""
    ordered: tuple[tuple[str, str | None], ...] = (
        (UNIT_NAME, unit_text),
        *sorted(drop_ins, key=lambda item: item[0]),
    )

    def pick(found: PathObservation | None, item: tuple[str, str | None]) -> PathObservation | None:
        name, text = item
        value = None if text is None else _path_in(text)
        return found if value is None else PathObservation(path=value, source=name)

    return reduce(pick, ordered, None)


def read_chair_path(unit_dir: Path) -> PathObservation | None:
    """Edge: read the unit file and its *.conf drop-ins under unit_dir, then decide."""
    unit = unit_dir / UNIT_NAME
    unit_text = unit.read_text(encoding="utf-8") if unit.is_file() else None
    drop_ins = tuple((conf.name, conf.read_text(encoding="utf-8")) for conf in (unit_dir / DROP_IN_DIR).glob("*.conf"))
    return effective_path(unit_text, drop_ins)
