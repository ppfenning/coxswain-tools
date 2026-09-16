"""The CLI-surface release check: pure over facts the edge below gathers."""

from __future__ import annotations

import importlib.util
import re
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_tools.release_check import Drift

_POSITIONAL_SECTION = re.compile(r"positional arguments:\n(.*?)(?:\n\n|\Z)", re.DOTALL)
_SUBPARSER_BLOCK = re.compile(r"^( *)\{([^}]+)\}\n(?:\1 +\S.*\n?)+", re.MULTILINE)
_DOC_COMMAND = re.compile(r"`(cox(?: (?!-)[\w-]+)*)[^`]*`")
_GENERATOR_ENTRY = "parse_subcommands"  # docs/_cli.py's pure core: parse_subcommands(help_text) -> Iterable[str]


def _choices(help_text: str) -> set[str]:
    """A `{a,b,c}` line only names subcommands when it is followed by an
    indented per-choice list; a flag's or plain positional's choice list
    never gets one, so it is left for the leaf's own value, not a group."""
    section = _POSITIONAL_SECTION.search(help_text)
    if section is None:
        return set()
    block = _SUBPARSER_BLOCK.search(section.group(1))
    return set(block.group(2).split(",")) if block else set()


def walk_help(help_texts: Mapping[str, str], choices: Callable[[str], set[str]] = _choices) -> set[str]:
    return {
        f"{prefix} {name}"
        for prefix, text in help_texts.items()
        for name in choices(text)
        if not choices(help_texts.get(f"{prefix} {name}", ""))
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
    """Compares `cox --help` against what the umbrella's docs/_cli.py generator
    would build from it, never against docs/reference/cli/*.md in the tree
    (only index.md is committed there; the per-group pages are build output).
    A `generator_error` fact short-circuits to one Drift, not one per symbol."""
    from agent_tools.release_check import Drift

    generator_error = facts.get("generator_error")
    if generator_error:
        return [Drift("cli_surface", "docs/_cli.py", None, "cox --help", None, generator_error)]

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


def _leaf_texts(
    prefix: list[str], root: str, run: Callable[[list[str], str], tuple[int, str]]
) -> tuple[dict[str, str], str | None]:
    cmd = ["cox", *prefix, "--help"]
    code, text = run(cmd, root)
    if code != 0:
        return {}, f"'{' '.join(cmd)}' exited {code}; is cox on PATH?"
    texts = {" ".join(["cox", *prefix]): text}
    for name in _choices(text):
        child, error = _leaf_texts(prefix + [name], root, run)
        if error:
            return {}, error
        texts.update(child)
    return texts, None


def _load_generator(root: str):
    path = Path(root) / "coxswain" / "docs" / "_cli.py"
    spec = importlib.util.spec_from_file_location("_umbrella_docs_cli", path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"no generator at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generated_doc_commands(root: str, help_texts: Mapping[str, str]) -> tuple[dict[str, set[str]], str | None]:
    """Runs the umbrella's docs/_cli.py: its pure core, `parse_subcommands`,
    walks the already-fetched `cox --help` text into one page per command
    group; those pages are written to a scratch directory and read back with
    `commands_in_doc`, the same as a real docs build's output, so the check
    diffs against the build, not the tree. Doc pages keep the canonical
    docs/reference/cli/<group>.md label so a remediation message still points
    somewhere that exists once the scratch directory is gone."""
    try:
        parse_subcommands = getattr(_load_generator(root), _GENERATOR_ENTRY)
    except (OSError, ImportError, AttributeError) as exc:
        return {}, f"docs/_cli.py generator unavailable: {exc}"
    try:
        groups: dict[str, set[str]] = {}
        for command in walk_help(help_texts, parse_subcommands):
            groups.setdefault(_group(command), set()).add(command)
        pages: dict[str, set[str]] = {}
        with tempfile.TemporaryDirectory() as out_dir:
            for group, commands in groups.items():
                page = Path(out_dir) / f"{group}.md"
                page.write_text("".join(f"- `{command}`\n" for command in sorted(commands)))
                pages[f"docs/reference/cli/{group}.md"] = commands_in_doc(page.read_text())
    except Exception as exc:
        return {}, f"docs/_cli.py generator failed: {exc}"
    return pages, None


def gather_cli_facts(root: str, run: Callable[[list[str], str], tuple[int, str]]) -> dict:
    readme_paths = sorted(Path(root).glob("*/README.md"))
    readme_commands = {p.parent.name: commands_in_doc(p.read_text()) for p in readme_paths}
    help_texts, cox_error = _leaf_texts([], root, run)
    if cox_error:
        return {"generator_error": cox_error, "readme_commands": readme_commands}
    doc_commands, generator_error = _generated_doc_commands(root, help_texts)
    if generator_error:
        return {"generator_error": generator_error, "readme_commands": readme_commands}
    return {
        "cli_commands": walk_help(help_texts),
        "doc_commands": doc_commands,
        "readme_commands": readme_commands,
    }
