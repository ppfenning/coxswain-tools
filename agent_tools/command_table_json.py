"""The command table as JSON, for the Go CLI to embed as go/commands.json.

Regenerate on purpose only:

    python -m agent_tools.command_table_json
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from agent_tools import commands

PROG = "cox"


def _jsonable_kwargs(kwargs: dict) -> dict:
    """`type` is a class or callable in Python; JSON carries its `__name__`."""
    return {
        k: (v.__name__ if k == "type" and callable(v) else v)
        for k, v in kwargs.items()
    }


def _arg_json(arg: commands.Arg) -> dict:
    return {"flags": list(arg.flags), "kwargs": _jsonable_kwargs(arg.kwargs)}


def _command_json(row: commands.Command) -> dict:
    return {
        "name": row.name,
        "summary": row.summary,
        "slash": row.slash,
        "examples": list(row.examples),
        "args": [_arg_json(a) for a in row.args],
    }


def _group_json(group: commands.Group, rows: Sequence[commands.Command]) -> dict:
    return {
        "name": group.name,
        "help": group.help,
        "description": group.description,
        "epilog": group.epilog,
        "args": [_arg_json(a) for a in group.args],
        "commands": [_command_json(r) for r in rows],
    }


def table_json(
    table: Sequence[tuple[commands.Group, Sequence[commands.Command]]],
    prog: str,
    description: str,
) -> dict:
    """Groups and commands keep the table's order; handler and fn are dropped."""
    return {
        "prog": prog,
        "description": description,
        "groups": [_group_json(g, rows) for g, rows in table],
    }


def render(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def main() -> int:
    from agent_tools import cli

    doc = table_json(cli.COMMAND_TABLE, PROG, cli.build_parser().description)
    out = Path(__file__).resolve().parent.parent / "go" / "commands.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
