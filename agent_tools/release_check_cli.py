"""The CLI-surface release check: pure over facts the edge below gathers."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_tools.release_check import Drift

_POSITIONAL_SECTION = re.compile(r"positional arguments:\n(.*?)(?:\n\n|\Z)", re.DOTALL)
_SUBPARSER_BLOCK = re.compile(r"^( *)\{([^}]+)\}\n(?:\1 +\S.*\n?)+", re.MULTILINE)
_DOC_COMMAND = re.compile(r"`(cox(?: (?!-)[\w-]+)*)[^`]*`")


def _choices(help_text: str) -> set[str]:
    """A `{a,b,c}` line only names subcommands when it is followed by an
    indented per-choice list; a flag's or plain positional's choice list
    never gets one, so it is left for the leaf's own value, not a group."""
    section = _POSITIONAL_SECTION.search(help_text)
    if section is None:
        return set()
    block = _SUBPARSER_BLOCK.search(section.group(1))
    return set(block.group(2).split(",")) if block else set()


def walk_help(help_texts: Mapping[str, str]) -> set[str]:
    return {
        f"{prefix} {name}"
        for prefix, text in help_texts.items()
        for name in _choices(text)
        if not _choices(help_texts.get(f"{prefix} {name}", ""))
    }


def commands_in_doc(text: str) -> set[str]:
    return {m.group(1) for m in _DOC_COMMAND.finditer(text)}


def _group(command: str) -> str:
    return command.split()[1]


def _doc_target(doc_commands: Mapping[str, set[str]], command: str) -> str:
    group = _group(command)
    return next((path for path in doc_commands if Path(path).stem == group),
                f"docs/reference/cli/{group}.md")


def check_cli_surface(facts: Mapping) -> list[Drift]:
    from agent_tools.release_check import Drift

    cli_commands: set[str] = facts.get("cli_commands", set())
    doc_commands: Mapping[str, set[str]] = facts.get("doc_commands", {})
    readme_commands: Mapping[str, set[str]] = facts.get("readme_commands", {})
    documented = {cmd for cmds in doc_commands.values() for cmd in cmds}
    missing_docs = [
        Drift("cli_surface", "cox --help", None, _doc_target(doc_commands, cmd), None,
              f"add {cmd} to {_doc_target(doc_commands, cmd)}")
        for cmd in sorted(cli_commands - documented)
    ]
    missing_readme = [
        Drift("cli_surface", "cox --help", None, f"{_group(cmd)}/README.md", None,
              f"add {cmd} to {_group(cmd)}/README.md")
        for cmd in sorted(cli_commands)
        if _group(cmd) in readme_commands and cmd not in readme_commands[_group(cmd)]
    ]
    stray_docs = [
        Drift("cli_surface", path, None, "cox --help", None, f"remove {cmd} from {path}")
        for path, cmds in sorted(doc_commands.items())
        for cmd in sorted(cmds)
        if cmd not in cli_commands
    ]
    stray_readmes = [
        Drift("cli_surface", f"{name}/README.md", None, "cox --help", None,
              f"remove {cmd} from {name}/README.md")
        for name, cmds in sorted(readme_commands.items())
        for cmd in sorted(cmds)
        if cmd not in cli_commands
    ]
    return missing_docs + missing_readme + stray_docs + stray_readmes


def _leaf_texts(prefix: list[str], root: str, run: Callable[[list[str], str], tuple[int, str]]) -> dict[str, str]:
    _, text = run(["cox", *prefix, "--help"], root)
    texts = {" ".join(["cox", *prefix]): text}
    for name in _choices(text):
        texts.update(_leaf_texts(prefix + [name], root, run))
    return texts


def gather_cli_facts(root: str, run: Callable[[list[str], str], tuple[int, str]]) -> dict:
    help_texts = _leaf_texts([], root, run)
    cli_docs_dir = Path(root) / "coxswain" / "docs" / "reference" / "cli"
    doc_paths = sorted(cli_docs_dir.glob("*.md")) if cli_docs_dir.is_dir() else []
    readme_paths = sorted(Path(root).glob("*/README.md"))
    return {
        "cli_commands": walk_help(help_texts),
        "doc_commands": {str(p): commands_in_doc(p.read_text()) for p in doc_paths},
        "readme_commands": {p.parent.name: commands_in_doc(p.read_text()) for p in readme_paths},
    }
