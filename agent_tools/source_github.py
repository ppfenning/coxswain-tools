from __future__ import annotations

import shlex
from typing import Mapping, Sequence

from agent_tools.sources import Candidate, Ref, SourceConfig

INTAKE_LABEL = "intake"
TAKEN_LABEL = "intake:taken"


def _repo(raw: Mapping) -> str:
    return raw["repository"]["nameWithOwner"]


def _labels(raw: Mapping) -> frozenset[str]:
    return frozenset(label["name"] for label in raw.get("labels", ()))


def _issue_number(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1]


def _wanted_label(config: SourceConfig) -> str:
    return config.filter.removeprefix("label:") if config.filter else INTAKE_LABEL


def candidates(config: SourceConfig, listing: Sequence[Mapping]) -> tuple[Ref, ...]:
    label = _wanted_label(config)
    return tuple(
        Ref(link=raw["url"], repo=_repo(raw))
        for raw in listing
        if label in _labels(raw) and _repo(raw) in config.repos
    )


def read(raw: Mapping) -> Candidate:
    return Candidate(
        title=raw["title"], body=raw["body"], repo=_repo(raw), link=raw["url"]
    )


def taken(link: str, intake_links: frozenset[str]) -> bool:
    return link in intake_links


def list_argv(config: SourceConfig, repo: str) -> list[str]:
    """`gh issue list` takes one repo per call; the edge loops over config.repos."""
    return [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--label",
        _wanted_label(config),
        "--json",
        "number,title,body,labels,url,repository",
    ]


def mark_argv(ref: Ref, intake_path: str) -> list[str]:
    """One argv per the protocol; `sh -c` chains the label swap and the comment."""
    number = shlex.quote(_issue_number(ref.link))
    repo = shlex.quote(ref.repo)
    edit = (
        f"gh issue edit {number} --repo {repo} "
        f"--remove-label {INTAKE_LABEL} --add-label {TAKEN_LABEL}"
    )
    comment = f"gh issue comment {number} --repo {repo} --body {shlex.quote(intake_path)}"
    return ["sh", "-c", f"{edit} && {comment}"]
