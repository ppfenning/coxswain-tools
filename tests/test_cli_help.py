import pytest

from agent_tools.cli import build_parser, main
from conftest import strip_ansi


def _choice_help(action):
    """Maps each subcommand name to its `help=` text for one subparsers
    action, reading the pseudo-actions argparse keeps the listing text in."""
    return {pa.dest: pa.help for pa in action._choices_actions}


def _walk(parser):
    """Yields (name, help) for every subparser reachable below `parser`, at
    any depth, by following `parser._subparsers` recursively."""
    if parser._subparsers is None:
        return
    for group_action in parser._subparsers._group_actions:
        help_by_name = _choice_help(group_action)
        for name, subparser in group_action.choices.items():
            yield name, help_by_name.get(name)
            yield from _walk(subparser)


def test_every_reachable_subcommand_has_help():
    missing = [name for name, help_text in _walk(build_parser()) if not help_text]
    assert missing == []


@pytest.mark.parametrize("group", ["runs", "epic", "hud", "plan", "route"])
def test_bare_group_prints_its_help_and_exits_2(group, capsys, monkeypatch):
    monkeypatch.setenv("PYTHON_COLORS", "0")
    monkeypatch.setenv("NO_COLOR", "1")
    assert main([group]) == 2
    assert strip_ansi(capsys.readouterr().out).startswith(f"usage: cox {group}")


def test_bare_setup_keeps_its_tui_fallback_not_group_help(capsys, monkeypatch):
    """setup is the one group excluded from `_bare_group`: its own bare
    invocation already had a graceful fallback (`_setup_tui`), so a bare
    `cox setup` prints that fallback's one-line reason, not the group's
    `--help` text, and this pins that deviation instead of leaving it
    undocumented."""
    monkeypatch.setenv("PYTHON_COLORS", "0")
    monkeypatch.setenv("NO_COLOR", "1")
    assert main(["setup"]) == 2
    assert not strip_ansi(capsys.readouterr().out).startswith("usage: cox setup")


def test_top_level_epilog_mentions_setup_doctor():
    assert "setup doctor" in build_parser().epilog
