from __future__ import annotations

from pathlib import Path

from agent_tools import cli, commands
from agent_tools import readme_commands as rc

README = Path(__file__).resolve().parent.parent / "README.md"

TEXT = f"intro\n\n{rc.BEGIN}\n```\nold line\n```\n{rc.END}\n\noutro: `keep`\n"
BLOCK = "```\ncox a b X   new line\n```\n"


def test_readme_block_matches_the_command_table():
    current = rc.block_of(README.read_text(encoding="utf-8"))
    assert current is not None, "README.md has no Commands markers"
    diff = rc.first_diff(rc.render_block(cli.COMMAND_TABLE), current)
    assert diff is None, (
        f"README.md Commands block differs from COMMAND_TABLE at line {diff and diff[0]}:\n"
        f"  rendered: {diff and diff[1]}\n  README:   {diff and diff[2]}\n"
        "fix: run `python -m agent_tools.readme_commands` with no flag to rewrite the block, then commit README.md"
    )


def test_first_diff_names_an_added_stub_row():
    group, rows = cli.COMMAND_TABLE[0]
    stub = commands.Command("stub", group.name, "a stub row", (commands.Arg(("--x",)),), None, False, ())
    table = [(group, [*rows, stub]), *cli.COMMAND_TABLE[1:]]
    line = f"cox {group.name} stub [--x X]   a stub row"
    diff = rc.first_diff(rc.render_block(table), rc.render_block(cli.COMMAND_TABLE))
    assert diff is not None
    assert diff[:2] == (len(rows) + 2, line)


def test_first_diff_is_none_when_equal():
    assert rc.first_diff(BLOCK, BLOCK) is None


def test_usage_forms():
    row = commands.Command(
        "go", "g", "goes",
        (
            commands.Arg(("id",)), commands.Arg(("opt",), {"nargs": "?"}), commands.Arg(("--n",), {"required": True}),
            commands.Arg(("--on",), {"action": "store_true"}), commands.Arg(("--lvl",), {"choices": ("a", "b")}),
            commands.Arg(("-v", "--verbose"), {"action": "store_true"}),
        ),
        None, False, (),
    )
    group = commands.Group("g", "", "", "")
    assert rc.render_block([(group, [row])]) == "```\ncox g go ID [OPT] --n N [--on] [--lvl a|b] [-v]   goes\n```\n"


def test_splice_is_idempotent():
    once = rc.splice(TEXT, BLOCK)
    assert once is not None
    assert rc.splice(once, BLOCK) == once


def test_splice_leaves_text_outside_the_markers_untouched():
    new = rc.splice(TEXT, BLOCK)
    assert new == TEXT.replace("```\nold line\n```\n", BLOCK)
    assert new.startswith("intro\n\n") and new.endswith("\n\noutro: `keep`\n")


def test_check_exits_one_naming_the_line_on_a_drifted_readme_and_writes_nothing(tmp_path, capsys):
    drifted = tmp_path / "README.md"
    drifted.write_text(TEXT, encoding="utf-8")
    assert rc.main(["--check", "--readme", str(drifted)]) == 1
    out = capsys.readouterr().out
    assert "differs at line 2" in out and "README:   old line" in out
    assert drifted.read_text(encoding="utf-8") == TEXT


def test_main_rewrites_then_check_passes(tmp_path):
    target = tmp_path / "README.md"
    target.write_text(TEXT, encoding="utf-8")
    assert rc.main(["--readme", str(target)]) == 0
    assert rc.main(["--check", "--readme", str(target)]) == 0
    assert target.read_text(encoding="utf-8").endswith(f"{rc.END}\n\noutro: `keep`\n")


def test_splice_without_markers_is_none():
    assert rc.splice("no markers here\n", BLOCK) is None
    assert rc.splice(f"{rc.BEGIN}\nno end\n", BLOCK) is None
