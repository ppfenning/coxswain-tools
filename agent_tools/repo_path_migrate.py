"""Pure rewrite of recorded repo paths to the tilde form, for queue rows and work-file text."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from agent_tools import repo_path, route

__all__ = ["Change", "migrate_row", "migrate_text", "plan"]


class Change(TypedDict):
    """One value that moves: `key` is a row key or a relative file path."""

    key: str
    old: str
    new: str


def _contract_repo(value: Any, home: str) -> Any:
    return repo_path.contract(value, home) if isinstance(value, str) else value


def _contract_surface(value: Any, home: str) -> Any:
    """Only an absolute entry is a candidate; a relative surface like `agent_tools/cli.py` stays."""
    if isinstance(value, str) and value.startswith("/"):
        return repo_path.contract(value, home)
    return value


def migrate_row(row: Mapping[str, Any], home: str) -> dict[str, Any] | None:
    """A new row with `extra["repo"]` and absolute `surfaces` entries contracted; None when nothing changes."""
    extra = row.get("extra") or {}
    surfaces = list(row.get("surfaces") or [])
    new_repo = _contract_repo(extra.get("repo"), home)
    new_surfaces = [_contract_surface(s, home) for s in surfaces]
    if new_repo == extra.get("repo") and new_surfaces == surfaces:
        return None
    new_extra = {**extra, "repo": new_repo} if "repo" in extra else dict(extra)
    changed: dict[str, Any] = {"extra": new_extra}
    if "surfaces" in row:
        changed["surfaces"] = new_surfaces
    return {**row, **changed}


_ITEM = re.compile(r"[^,\[\]]+")


def _is_block_item(line: str) -> bool:
    return line[:1].isspace() or line.startswith("- ") or line.rstrip() == "-"


def _swap_block_item(line: str, moves: Mapping[str, str]) -> str:
    stripped = line.strip()
    if not stripped.startswith("-"):
        return line
    item = stripped[1:].strip()
    quote = item[0] if len(item) >= 2 and item[0] == item[-1] and item[0] in "'\"" else ""
    bare = item[1:-1] if quote else item
    if bare not in moves:
        return line
    start = line.index(item)
    return line[:start] + quote + moves[bare] + quote + line[start + len(item) :]


def _swap_flow_items(line: str, moves: Mapping[str, str]) -> str:
    head, bracket, tail = line.partition("[")
    swapped = _ITEM.sub(
        lambda m: m.group().replace(m.group().strip(), moves.get(m.group().strip(), m.group().strip()), 1),
        tail,
    )
    return head + bracket + swapped


def _swap_lines(lines: Sequence[str], repo_move: tuple[str, str] | None, moves: Mapping[str, str]) -> list[str]:
    out: list[str] = []
    in_block = False
    for line in lines:
        if line == "surfaces:":
            in_block = True
            out.append(line)
        elif in_block and (_is_block_item(line) or line == ""):
            out.append(_swap_block_item(line, moves))
        elif line.startswith("surfaces: ["):
            in_block = False
            out.append(_swap_flow_items(line, moves))
        elif repo_move and line.startswith("repo: ") and repo_move[0] in line:
            in_block = False
            out.append(line.replace(repo_move[0], repo_move[1], 1))
        else:
            in_block = False
            out.append(line)
    return out


def migrate_text(text: str, home: str) -> str | None:
    """The work-file text with only its `repo:` line and `surfaces` entries contracted; None when nothing changes."""
    fields, _ = route.parse_frontmatter(text)
    if not fields:
        return None
    repo = fields.get("repo")
    new_repo = _contract_repo(repo, home)
    repo_move = (repo, new_repo) if isinstance(repo, str) and new_repo != repo else None
    surfaces = fields.get("surfaces")
    moves = {s: n for s in surfaces if (n := _contract_surface(s, home)) != s} if isinstance(surfaces, list) else {}
    if repo_move is None and not moves:
        return None
    opening, closing = "---\n", "\n---\n"
    header_end = len(opening) + text[len(opening) :].find(closing)
    header = text[len(opening) : header_end]
    rewritten = "\n".join(_swap_lines(header.split("\n"), repo_move, moves))
    result = opening + rewritten + text[header_end:]
    return None if result == text else result


def _row_key(row: Mapping[str, Any]) -> str:
    return f"{row.get('kind')}:{row.get('initiative')}/{row.get('task_id')}"


def _moves(key: str, old: Sequence[Any], new: Sequence[Any]) -> list[Change]:
    return [{"key": key, "old": o, "new": n} for o, n in zip(old, new) if o != n]


def _row_changes(row: Mapping[str, Any], home: str) -> list[Change]:
    migrated = migrate_row(row, home)
    if migrated is None:
        return []
    key = _row_key(row)
    repos = _moves(key, [(row.get("extra") or {}).get("repo")], [migrated["extra"].get("repo")])
    return repos + _moves(key, row.get("surfaces") or [], migrated.get("surfaces") or [])


def _text_changes(path: str, text: str, home: str) -> list[Change]:
    migrated = migrate_text(text, home)
    if migrated is None:
        return []
    old, new = route.parse_frontmatter(text)[0], route.parse_frontmatter(migrated)[0]
    repos = _moves(path, [old.get("repo")], [new.get("repo")])
    return repos + _moves(path, old.get("surfaces") or [], new.get("surfaces") or [])


def plan(rows: Sequence[Mapping[str, Any]], files: Mapping[str, str], home: str) -> list[Change]:
    """Every value that would move: rows in the order given, then files by sorted path."""
    from_rows = [c for row in rows for c in _row_changes(row, home)]
    from_files = [c for path in sorted(files) for c in _text_changes(path, files[path], home)]
    return from_rows + from_files
