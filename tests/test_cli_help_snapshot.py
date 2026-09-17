"""Byte-for-byte `--help` snapshots of the `runs` group and every subcommand.

The fixtures live under tests/fixtures/help/runs/, so the command-table
migration (docs/design/command-table.md) is proven to leave the help output
identical. Regenerate them on purpose only:

    python -m tests.test_cli_help_snapshot --write
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pytest

from agent_tools import cli

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "help" / "runs"
GROUP = "runs"
COLUMNS = "100"  # argparse wraps at the terminal width; pin it so the text is stable


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


def help_texts() -> dict[str, str]:
    """fixture stem -> help text, for the group and each of its subcommands."""
    os.environ["COLUMNS"] = COLUMNS
    group = _subparsers(cli.build_parser())[GROUP]
    texts = {GROUP: group.format_help()}
    for name, sub in _subparsers(group).items():
        texts[f"{GROUP}-{name}"] = sub.format_help()
    return texts


def _write() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for stem, text in help_texts().items():
        (FIXTURES / f"{stem}.txt").write_text(text)
        print(f"wrote {FIXTURES / f'{stem}.txt'}")


@pytest.mark.parametrize("stem", sorted(p.stem for p in FIXTURES.glob("*.txt")))
def test_help_matches_its_fixture(stem: str, monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", COLUMNS)
    assert help_texts()[stem] == (FIXTURES / f"{stem}.txt").read_text()


def test_every_subcommand_has_a_fixture_and_no_fixture_is_stale(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", COLUMNS)
    assert set(help_texts()) == {p.stem for p in FIXTURES.glob("*.txt")}


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        _write()
    else:
        print(__doc__)
