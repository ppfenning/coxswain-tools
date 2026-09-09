"""The readmes check: dead pre-rename repo names and un-versioned docs links in each component's README."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from agent_tools.release_check_pages import alias_sentences

if TYPE_CHECKING:
    from agent_tools.release_check import Drift

MARKERS = ("alias", "deprecated")
_DOC_LINK_RE = re.compile(r"https?://\S+/coxswain/(\S*)")
_SITE_URL_RE = re.compile(r"site_url:\s*\S*/([^/\s]+)/?\s*$", re.MULTILINE)


def old_to_new(current: set[str]) -> dict[str, str]:
    return {f"agent-{name[len('coxswain-'):]}": name for name in current if name.startswith("coxswain-")}


def _hits(text: str, mapping: Mapping[str, str]) -> list[tuple[int, str, str]]:
    raw = [(m.start(), old) for old in mapping for m in re.finditer(rf"\b{re.escape(old)}\b", text)]
    return sorted((text[:start].count("\n") + 1, old, f"rename {old} to {mapping[old]}") for start, old in raw)


def dead_names(text: str, current: set[str]) -> list[tuple[int, str]]:
    return [(line, correction) for line, _, correction in _hits(text, old_to_new(current))]


def doc_link_drifts(text: str, docs_base: str) -> list[tuple[int, str]]:
    return [
        (text[: m.start()].count("\n") + 1, "add the version segment")
        for m in _DOC_LINK_RE.finditer(text)
        if m.group(1).split("/", 1)[0] != docs_base
    ]


def check_readmes(facts: Mapping) -> list[Drift]:
    from agent_tools.release_check import Drift

    repo_names = set(facts.get("repo_names") or ())
    docs_base = facts.get("docs_base", "latest")
    readmes = facts.get("readmes") or {}
    current = {repo.split("/")[-1] for repo in repo_names}
    mapping = old_to_new(current)

    def readme_drifts(name: str, text: str) -> list[Drift]:
        readme_file = f"{name}/README.md"
        exempt = {
            (old, line)
            for old in mapping
            for line, sentence in alias_sentences(text, (old,))
            if any(marker in sentence.lower() for marker in MARKERS)
        }
        first_line: dict[str, int] = {}
        for line, old, correction in _hits(text, mapping):
            if (old, line) not in exempt:
                first_line.setdefault(correction, line)
        dead = [Drift("readmes", readme_file, line, readme_file, line, correction) for correction, line in first_line.items()]
        links = [
            Drift("readmes", readme_file, line, readme_file, line, correction)
            for line, correction in doc_link_drifts(text, docs_base)
        ]
        return dead + links

    return [d for name, text in readmes.items() for d in readme_drifts(name, text)]


def resolve_docs_base(mkdocs_path: str) -> str:
    text = Path(mkdocs_path).read_text() if Path(mkdocs_path).exists() else ""
    match = _SITE_URL_RE.search(text)
    return match.group(1) if match else "latest"


def gather_readmes_facts(readme_paths: Mapping[str, str], manifest: Mapping, docs_base: str) -> dict:
    readmes = {name: Path(path).read_text() for name, path in readme_paths.items() if Path(path).exists()}
    repo_names = {spec["repo"] for spec in manifest.get("components", {}).values() if spec.get("repo")}
    return {"readmes": readmes, "repo_names": repo_names, "docs_base": docs_base}
