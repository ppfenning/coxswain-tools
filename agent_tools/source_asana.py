from __future__ import annotations

import json
import shlex
from collections.abc import Mapping, Sequence

from agent_tools.sources import Candidate, Ref, SourceConfig

API = "https://app.asana.com/api/1.0"
DEFAULT_TAG = "intake"
FIELDS = "name,notes,permalink_url,tags.name,memberships.project.gid,memberships.section.name"
MARK_NEEDS_TOKEN_ENV = True


def unwrap(payload: Mapping, project: str) -> list[dict]:
    """Asana wraps a listing in `data`; each task is stamped with the project it was listed from."""
    return [{**task, "listed_in": project} for task in payload["data"]]


def _project(raw: Mapping) -> str:
    return raw["listed_in"]


def _markers(raw: Mapping) -> frozenset[str]:
    tags = (f"tag:{tag['name']}" for tag in raw.get("tags", ()))
    sections = (
        f"section:{m['section']['name']}"
        for m in raw.get("memberships", ())
        if m.get("section") and m["project"]["gid"] == _project(raw)
    )
    return frozenset((*tags, *sections))


def _wanted(config: SourceConfig) -> str:
    return config.filter or f"tag:{DEFAULT_TAG}"


def _task_gid(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1]


def candidates(config: SourceConfig, listing: Sequence[Mapping]) -> tuple[Ref, ...]:
    """The filter is `tag:<name>` or `section:<name>`; repos are Asana project gids."""
    return tuple(
        Ref(link=raw["permalink_url"], repo=_project(raw))
        for raw in listing
        if _wanted(config) in _markers(raw) and _project(raw) in config.repos
    )


def read(raw: Mapping) -> Candidate:
    return Candidate(
        title=raw["name"], body=raw["notes"], repo=_project(raw), link=raw["permalink_url"]
    )


def taken(link: str, intake_links: frozenset[str]) -> bool:
    return link in intake_links


def _curl(token_env: str, *args: str) -> list[str]:
    header = f'"Authorization: Bearer ${{{token_env}}}"'
    return ["sh", "-c", " ".join(("curl", "-sSf", "-H", header, *args))]


def list_argv(config: SourceConfig, project: str) -> list[str]:
    url = f"{API}/tasks?project={project}&opt_fields={FIELDS}"
    return _curl(config.token_env, shlex.quote(url))


def mark_argv(ref: Ref, intake_path: str, token_env: str) -> list[str]:
    """One comment on the task and no status move; the edge passes `config.token_env`."""
    body = json.dumps({"data": {"text": f"Taken into intake: {intake_path}"}})
    url = f"{API}/tasks/{_task_gid(ref.link)}/stories"
    return _curl(
        token_env, "-X", "POST", "-H", "'Content-Type: application/json'",
        "-d", shlex.quote(body), shlex.quote(url),
    )
