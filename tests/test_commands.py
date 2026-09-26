"""`commands.build_parser` on rows that carry their own subcommands."""

from __future__ import annotations

import argparse

from agent_tools import commands


def _leaf(name: str, handler, **kw) -> commands.Command:
    return commands.Command(name, "g", f"{name} summary", (commands.Arg(("--n",), {"type": int}),), handler, False, (), **kw)


def _parser(row: commands.Command) -> argparse.ArgumentParser:
    group = commands.Group(name="g", help="h", description="d", epilog="e")
    top = argparse.ArgumentParser(prog="cox")
    commands.build_parser([row], [group], top.add_subparsers(dest="top"))
    return top


def test_a_nested_row_builds_three_levels_and_its_subcommand_dispatches_with_its_defaults() -> None:
    child = _leaf("run", lambda _a: 7, defaults={"graph": "epic"})
    row = commands.Command("launch", "g", "s", (), None, False, (), subcommands=(child,), sub_dest="graph", sub_required=True)
    ns = _parser(row).parse_args(["g", "launch", "run", "--n", "3"])
    assert (ns.fn(ns), ns.graph, ns.n) == (7, "epic", 3)


def test_a_nested_row_with_no_handler_prints_its_own_help_and_returns_2(capsys) -> None:
    row = commands.Command("chair", "g", "the chair lock", (), None, False, (), subcommands=(_leaf("take", lambda _a: 0),), sub_dest="chair_cmd")
    ns = _parser(row).parse_args(["g", "chair"])
    assert ns.fn(ns) == 2
    assert "usage: cox g chair" in capsys.readouterr().out


def test_a_row_description_shows_one_statement_per_line_and_a_row_without_one_is_unchanged() -> None:
    described = commands.Command("d", "g", "s", (), lambda _a: 0, False, (), description="first statement here\nsecond statement here")
    group = commands.Group(name="g", help="h", description="d", epilog="e")
    top = argparse.ArgumentParser(prog="cox")
    group_parser = commands.build_parser([described, _leaf("p", lambda _a: 0)], [group], top.add_subparsers(dest="top"))["g"]
    subs = next(a for a in group_parser._actions if isinstance(a, argparse._SubParsersAction)).choices
    assert "first statement here\nsecond statement here" in subs["d"].format_help()
    assert (subs["p"].description, type(subs["p"]._get_formatter()).__name__) == (None, "HelpFormatter")
