from __future__ import annotations

import importlib
import importlib.metadata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal, Protocol, runtime_checkable

from agent_tools import route

Kind = Literal["issue", "pr"]


@dataclass(frozen=True)
class Ref:
    link: str
    repo: str
    kind: Kind = "issue"


@dataclass(frozen=True)
class Candidate:
    title: str
    body: str
    repo: str
    link: str
    kind: Kind = "issue"


@dataclass(frozen=True)
class SourceConfig:
    repos: tuple[str, ...]
    filter: str
    token_env: str


@runtime_checkable
class SourceAdapter(Protocol):
    candidates: Callable[[SourceConfig, Sequence[Mapping]], tuple[Ref, ...]]
    read: Callable[[Mapping], Candidate]
    taken: Callable[[str, frozenset[str]], bool]
    mark_argv: Callable[[Ref, str], list[str]]


def source_config(profile: Mapping, name: str) -> SourceConfig | None:
    block = profile.get("sources", {}).get(name)
    if block is None:
        return None
    return SourceConfig(
        repos=tuple(block.get("repos", ())),
        filter=block.get("filter", ""),
        token_env=block.get("token_env", ""),
    )


def intake_links(files: Mapping[str, str]) -> frozenset[str]:
    fields = (route.parse_frontmatter(text)[0] for text in files.values())
    links = (field.get("link") for field in fields)
    return frozenset(link for link in links if link is not None)


# Adapters that name a vendor live outside this repository and register here by name (Pat, 2026-09-25).
ENTRY_POINT_GROUP = "coxswain.sources"


def adapter_for(name: str):
    """The built-in `agent_tools.source_<name>`, else the adapter an installed package registers under `coxswain.sources`; None for neither."""
    try:
        return importlib.import_module(f"agent_tools.source_{name}")
    except ImportError:
        pass
    registered = [ep for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]
    return registered[0].load() if registered else None


def FakeAdapter(listing: Sequence[Mapping]) -> SimpleNamespace:
    """An in-memory adapter over a literal listing, for tests and `--dry-run`."""
    fixed_listing = tuple(listing)

    def candidates(config: SourceConfig, _listing: Sequence[Mapping]) -> tuple[Ref, ...]:
        return tuple(
            Ref(link=raw["link"], repo=raw["repo"])
            for raw in fixed_listing
            if raw["repo"] in config.repos
        )

    def read(raw: Mapping) -> Candidate:
        return Candidate(
            title=raw["title"], body=raw["body"], repo=raw["repo"], link=raw["link"]
        )

    def taken(link: str, intake_links: frozenset[str]) -> bool:
        return link in intake_links

    def mark_argv(ref: Ref, intake_path: str) -> list[str]:
        return ["echo", "marked", ref.link, intake_path]

    return SimpleNamespace(
        candidates=candidates, read=read, taken=taken, mark_argv=mark_argv
    )
