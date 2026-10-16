from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence

from agent_tools.sources import Candidate, Kind, Ref, SourceConfig

INTAKE_LABEL = "intake"
REVIEW_LABEL = "review"
REVIEW_TAKEN_LABEL = "review:taken"
TAKEN_LABEL = "intake:taken"


def _repo(raw: Mapping) -> str:
    """From the url: `gh pr list` has no `repository` field, and `gh issue list` dropped it (gh 2.102)."""
    return raw["repository"]["nameWithOwner"] if "repository" in raw else "/".join(raw["url"].split("/")[3:5])


def _kind(raw: Mapping) -> Kind:
    return "pr" if "/pull/" in raw["url"] else "issue"


def _labels(raw: Mapping) -> frozenset[str]:
    return frozenset(label["name"] for label in raw.get("labels", ()))


def _issue_number(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1]


def _wanted_label(config: SourceConfig) -> str:
    return config.filter.removeprefix("label:") if config.filter else INTAKE_LABEL


def _label_for(config: SourceConfig, kind: Kind) -> str:
    return REVIEW_LABEL if kind == "pr" else _wanted_label(config)


def candidates(config: SourceConfig, listing: Sequence[Mapping]) -> tuple[Ref, ...]:
    return tuple(
        Ref(link=raw["url"], repo=_repo(raw), kind=_kind(raw))
        for raw in listing
        if _label_for(config, _kind(raw)) in _labels(raw) and _repo(raw) in config.repos
    )


def read(raw: Mapping) -> Candidate:
    return Candidate(
        title=raw["title"], body=raw["body"], repo=_repo(raw), link=raw["url"], kind=_kind(raw)
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
        "number,title,body,labels,url",
    ]


def pr_list_argv(config: SourceConfig, repo: str) -> list[str]:
    return ["gh", "pr", "list", "--repo", repo, "--state", "open", "--label", REVIEW_LABEL, "--json", "number,title,body,labels,url"]


_PROJECT_QUERY = (
    "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){projectsV2(first:20){nodes{"
    "items(first:100){nodes{id type content{"
    "... on Issue{title body url state closedByPullRequestsReferences(first:1){totalCount}} "
    "... on DraftIssue{title body} ... on PullRequest{url}}}}}}}}"
)
# Flattens the GraphQL reply to a list of {id, type, title, body, url, state, linked_prs} so the edge can extend one listing.
_PROJECT_FLATTEN = (
    "[.data.repository.projectsV2.nodes[].items.nodes[] | {id, type, title: .content.title, "
    "body: .content.body, url: .content.url, state: .content.state, linked_prs: (.content.closedByPullRequestsReferences.totalCount // 0)}]"
)
_PROJECT_TYPES = frozenset({"ISSUE", "DRAFT_ISSUE"})


def project_list_argv(config: SourceConfig, repo: str) -> list[str]:
    """The items of the projects a repository belongs to, flattened by `--jq` to a JSON list."""
    owner, _, name = repo.partition("/")
    return [
        "gh",
        "api",
        "graphql",
        "-f",
        f"query={_PROJECT_QUERY}",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "--jq",
        _PROJECT_FLATTEN,
    ]


def project_candidates(listing: Sequence[Mapping], config: SourceConfig) -> tuple[Candidate, ...]:
    """Open issue items whose url is in a configured repo, as the labelled path reads open issues only; an item with a linked pull request is left out."""
    return tuple(
        Candidate(
            title=raw["title"] or "",
            body=raw.get("body") or "",
            repo=_repo(raw),
            link=raw["url"],
            project_item_id=raw["id"],
        )
        for raw in listing
        if raw.get("type") in _PROJECT_TYPES
        and raw.get("url")
        and raw.get("state") == "OPEN"
        and not raw.get("linked_prs")
        and _repo(raw) in config.repos
    )


def _pr_mark_argv(ref: Ref) -> list[str]:
    edit = ["gh", "pr", "edit", _issue_number(ref.link), "--repo", ref.repo]
    return [*edit, "--remove-label", REVIEW_LABEL, "--add-label", REVIEW_TAKEN_LABEL]


def mark_argv(ref: Ref, intake_path: str) -> list[str]:
    """One argv per the protocol; `sh -c` chains the label swap and the comment."""
    if ref.kind == "pr":
        return _pr_mark_argv(ref)
    number = shlex.quote(_issue_number(ref.link))
    repo = shlex.quote(ref.repo)
    edit = (
        f"gh issue edit {number} --repo {repo} "
        f"--remove-label {INTAKE_LABEL} --add-label {TAKEN_LABEL}"
    )
    comment = f"gh issue comment {number} --repo {repo} --body {shlex.quote(intake_path)}"
    return ["sh", "-c", f"{edit} && {comment}"]
