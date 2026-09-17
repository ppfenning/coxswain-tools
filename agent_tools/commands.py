"""The command table: `Command` rows and the generator that folds them into
argparse subparsers, per docs/design/command-table.md.

A group's own parser (its help/description/epilog) is not part of a `Command`
row -- rows are leaf subcommands -- so callers also supply one `Group` per
distinct `group` value, carrying that group-level text.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Arg:
    """One `add_argument` call: positional or flag names, plus its kwargs."""

    flags: tuple[str, ...]
    kwargs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Command:
    name: str
    group: str
    summary: str
    args: tuple[Arg, ...]
    handler: Callable[[argparse.Namespace], int]
    slash: bool
    examples: tuple[str, ...]


@dataclass(frozen=True)
class Group:
    name: str
    help: str
    description: str
    epilog: str


def _bare_group(parser: argparse.ArgumentParser) -> Callable[[argparse.Namespace], int]:
    """Default `fn` for a group parser whose subcommand is optional: an
    operator who runs the group alone sees that group's own help and a exit
    code of 2, not a traceback or silence."""
    def _fn(_a: argparse.Namespace) -> int:
        parser.print_help()
        return 2
    return _fn


def build_parser(
    commands: list[Command],
    groups: list[Group],
    sub: argparse._SubParsersAction,
) -> dict[str, argparse.ArgumentParser]:
    """Fold `commands` into one subparser per row, under one parser per
    `groups` entry, attached to `sub`. Returns group name -> its parser."""
    by_group: dict[str, list[Command]] = {}
    for row in commands:
        by_group.setdefault(row.group, []).append(row)
    parsers: dict[str, argparse.ArgumentParser] = {}
    for g in groups:
        gp = sub.add_parser(
            g.name, help=g.help, description=g.description, epilog=g.epilog,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        gp.set_defaults(fn=_bare_group(gp))
        gp_sub = gp.add_subparsers(dest="cmd", required=False)
        for row in by_group.get(g.name, []):
            rp = gp_sub.add_parser(row.name, help=row.summary)
            for arg in row.args:
                rp.add_argument(*arg.flags, **arg.kwargs)
            rp.set_defaults(fn=row.handler)
        parsers[g.name] = gp
    return parsers
