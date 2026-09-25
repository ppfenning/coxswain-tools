"""go/commands.json is generated from COMMAND_TABLE and must not drift.

Regenerate on purpose only:

    python -m agent_tools.command_table_json
"""

from __future__ import annotations

from pathlib import Path

from agent_tools import cli, command_table_json, commands

COMMITTED = Path(__file__).resolve().parent.parent / "go" / "commands.json"


def test_committed_commands_json_matches_the_command_table() -> None:
    doc = command_table_json.table_json(
        cli.COMMAND_TABLE, "cox", cli.build_parser().description
    )
    assert COMMITTED.read_text() == command_table_json.render(doc), (
        "go/commands.json is stale: run `python -m agent_tools.command_table_json`"
    )


def test_a_type_float_kwarg_becomes_the_string_float_and_handler_and_fn_are_dropped() -> None:
    row = commands.Command(
        name="go", group="g", summary="s",
        args=(commands.Arg(("--n",), {"type": float, "default": 1.5}),),
        handler=lambda _a: 0, slash=False, examples=("cox g go",),
    )
    group = commands.Group(name="g", help="h", description="d", epilog="e", fn=None)
    doc = command_table_json.table_json([(group, [row])], "cox", "desc")
    cmd = doc["groups"][0]["commands"][0]
    assert cmd["args"] == [{"flags": ["--n"], "kwargs": {"type": "float", "default": 1.5}}]
    assert "handler" not in cmd and "fn" not in doc["groups"][0]
