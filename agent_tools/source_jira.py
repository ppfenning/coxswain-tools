"""Jira Data Center: bearer personal access token, `/rest/api/2/search`; Cloud needs basic auth and is not handled."""

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

from agent_tools.sources import Candidate, Ref, SourceConfig

TOKEN_ENV = "JIRA_TOKEN"
PAGE = 100
_JQL_FIELD = {"status": "status", "label": "labels"}
_UNWRAP = 'import json,sys; json.dump(json.load(sys.stdin)["issues"], sys.stdout)'


def _repo(raw: Mapping) -> str:
    """`<host>/<PROJECT>`, the shape a `sources.jira.repos` entry takes."""
    return f"{urlsplit(raw['self']).netloc}/{raw['fields']['project']['key']}"


def _link(raw: Mapping) -> str:
    return f"{raw['self'].split('/rest/api/')[0]}/browse/{raw['key']}"


def _matches(config: SourceConfig, raw: Mapping) -> bool:
    kind, _, value = config.filter.partition(":")
    fields = raw["fields"]
    if kind == "status":
        return fields["status"]["name"].casefold() == value.casefold()
    if kind == "label":
        return value in fields.get("labels", ())
    return False


def _refusal(config: SourceConfig) -> str | None:
    if config.token_env not in ("", TOKEN_ENV):
        return f"sources.jira.token_env must be {TOKEN_ENV}: mark_argv is given no config"
    if config.filter.partition(":")[0] not in _JQL_FIELD:
        return "sources.jira.filter must be status:<name> or label:<name>"
    return None


def _curl(*args: str) -> str:
    return " ".join(["curl", "-sSf", "-H", f'"Authorization: Bearer ${TOKEN_ENV}"', *args])


def _search(config: SourceConfig, repo: str) -> str:
    host, _, project = repo.rpartition("/")
    kind, _, value = config.filter.partition(":")
    jql = f'project = "{project}" AND {_JQL_FIELD[kind]} = "{value}" ORDER BY created DESC'
    url = shlex.quote(f"https://{host}/rest/api/2/search")
    query = ("--data-urlencode", shlex.quote(f"jql={jql}"), "--data-urlencode", f"maxResults={PAGE}")
    unwrap = f"{shlex.quote(sys.executable)} -c {shlex.quote(_UNWRAP)}"
    return f"{_curl('-G', url, *query)} | {unwrap}"


def candidates(config: SourceConfig, listing: Sequence[Mapping]) -> tuple[Ref, ...]:
    return tuple(
        Ref(link=_link(raw), repo=_repo(raw))
        for raw in listing
        if _matches(config, raw) and _repo(raw) in config.repos
    )


def read(raw: Mapping) -> Candidate:
    return Candidate(
        title=raw["fields"]["summary"],
        body=raw["fields"]["description"] or "",
        repo=_repo(raw),
        link=_link(raw),
    )


def taken(link: str, intake_links: frozenset[str]) -> bool:
    return link in intake_links


def list_argv(config: SourceConfig, repo: str) -> list[str]:
    """Newest first, one page of `PAGE`, as the bare `issues` list; refuses a config `mark_argv` cannot honour."""
    refusal = _refusal(config)
    script = f"echo {shlex.quote('jira: ' + refusal)} >&2; exit 2" if refusal else _search(config, repo)
    return ["sh", "-c", script]


def mark_argv(ref: Ref, intake_path: str) -> list[str]:
    base, _, key = ref.link.rstrip("/").rpartition("/browse/")
    url = shlex.quote(f"{base}/rest/api/2/issue/{key}/comment")
    body = shlex.quote(json.dumps({"body": intake_path}))
    post = _curl("-X", "POST", "-H", "'Content-Type: application/json'", "--data", body, url)
    return ["sh", "-c", post]
