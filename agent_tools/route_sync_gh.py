"""The thin `gh` edge for the one-way GitHub Projects mirror: the only
module in the tree that runs `gh`. Every function here returns `(ok,
value)` or `(ok, detail)` rather than raising, and `execute` walks a plan
one `(kind, ok, detail)` per step, `agent_tools/land.py`'s own edge shape.
Gate resolution is a stand-in for the cartridge `policy.gate` lookup
landing separately: read straight off the item's `gate:` frontmatter.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agent_tools import epic, records, route, route_sync

__all__ = [
    "auth_ok", "create_project", "execute", "existing", "existing_item", "find_project", "items_from_store",
    "project_item_from_response",
]

_LABEL = "coxswain"

_STATE_MUTATION = (
    "mutation($project:ID!,$item:ID!,$field:ID!,$option:String!){"
    "updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,"
    "fieldId:$field,value:{singleSelectOptionId:$option}}){projectV2Item{id}}}"
)


def auth_ok(run) -> bool:
    """Whether `gh` is authenticated, per `gh auth status`'s exit code."""
    return run(["gh", "auth", "status"], capture_output=True, text=True).returncode == 0


def _origin_slug(checkout: str) -> str:
    """`owner/name` from `<checkout>/.git/config`'s origin url, or `""`."""
    config = Path(checkout) / ".git" / "config"
    if not checkout or not config.is_file():
        return ""
    url = next((ln.split("=", 1)[1].strip() for ln in config.read_text().splitlines()
                if ln.strip().startswith("url")), "")
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1) if match else ""


def _repo_of(value: str) -> str:
    """`value` may be a checkout path (`route file --repo PATH`'s own
    contract for intake items) or an `owner/name` slug already: resolve it
    as a checkout first, and fall back to the literal value when there is
    nothing on disk to resolve, rather than assuming which shape it is."""
    return _origin_slug(value) or value


def _initiative_repo(root: Path, initiative: str) -> str:
    meta = root / "work" / initiative / "initiative.md"
    if not meta.is_file():
        return ""
    fields, _ = route.parse_frontmatter(meta.read_text())
    return _origin_slug(fields.get("repo", ""))


def _pid_alive(root: Path, run_id: str) -> bool:
    pid_path = root / "runs" / f"{run_id}.pid"
    if not pid_path.is_file():
        return False
    text = pid_path.read_text().strip()
    return text.isdigit() and epic.run_live(int(text), pid_path)


def _run_cost(root: Path, run_id: str) -> float:
    usage_path = root / "runs" / f"{run_id}.usage.json"
    if not usage_path.is_file():
        return 0.0
    try:
        usage = records.load_usage(usage_path)
    except json.JSONDecodeError:
        return 0.0
    return records.usage_summary(usage).get("cost_usd", 0.0)


def _item(fields: dict, body: str, stem: str, *, state: str, phase: str, run_id: str,
          cost: float, gate: str, repo: str) -> route_sync.Item:
    return route_sync.Item(
        id=fields.get("id", stem), title=fields.get("title", stem), body=body, repo=repo,
        state=state, phase=phase, run=run_id, cost_usd=cost, gate=gate, issue=fields.get("issue"),
    )


def _intake_item(path: Path) -> route_sync.Item:
    fields, body = route.parse_frontmatter(path.read_text())
    return _item(fields, body, path.stem, state="intake", phase="", run_id="", cost=0.0,
                 gate="", repo=_repo_of(fields.get("repo", "")))


def _work_item(root: Path, path: Path) -> route_sync.Item:
    fields, body = route.parse_frontmatter(path.read_text())
    initiative, phase_dir, stem = path.parts[-3], path.parts[-2], path.stem
    attempts = fields.get("attempts") or []
    run_id = attempts[-1] if attempts else ""
    in_flight = bool(run_id) and _pid_alive(root, run_id)
    cost = round(sum(_run_cost(root, a) for a in attempts), 4)
    state = "in_flight" if in_flight else fields.get("state", "todo")
    return _item(fields, body, stem, state=state, phase=fields.get("phase", phase_dir),
                 run_id=run_id, cost=cost, gate=fields.get("gate", ""),
                 repo=_initiative_repo(root, initiative))


def items_from_store(workspace) -> list[route_sync.Item]:
    """Every `intake/*.md` and `work/*/*/*.md` item, as a `route_sync.Item`."""
    root = Path(workspace)
    intake_dir = root / "intake"
    intake = [_intake_item(p) for p in sorted(intake_dir.glob("*.md"))] if intake_dir.is_dir() else []
    work = [_work_item(root, p) for p in sorted(root.glob("work/*/*/*.md"))]
    return intake + work


# `gh` returns 30 rows unless told otherwise; a missed row is replanned as new on every sync.
_LIMIT = "10000"
_FIELD_TITLES = {t.lower(): t for t in ("State", "Phase", "Run", "Cost", "Gate")}


def existing(run, repo_names: list[str], project: str | None):
    """`(True, (issues, project_items, item_node_ids))` on success, `(False,
    detail)` on the first failed `gh` call. `project` may be `None` — a
    dry-run preview before any project exists — in which case the item
    listing is skipped rather than run against a project that isn't there."""
    issues: dict[str, dict] = {}
    for repo in repo_names:
        result = run(["gh", "issue", "list", "--repo", repo, "--label", _LABEL, "--state", "all",
                      "--json", "number,title,body,state", "--limit", _LIMIT], capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        for row in json.loads(result.stdout or "[]"):
            issues[str(row["number"])] = {"title": row.get("title", ""), "body": row.get("body", ""),
                                          "state": row.get("state", "")}
    project_items: dict[str, dict] = {}
    item_node_ids: dict[str, str] = {}
    if project is not None:
        owner, _, number = project.partition("/")
        result = run(["gh", "project", "item-list", number, "--owner", owner, "--format", "json", "--limit", _LIMIT],
                     capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        # `gh project item-list --format json` keys field values by the
        # lowercased field title ("state"); plan compares by title ("State").
        for row in json.loads(result.stdout or "{}").get("items", []):
            key = str((row.get("content") or {}).get("number", ""))
            if not key:
                continue
            project_items[key] = {_FIELD_TITLES[k]: v for k, v in row.items() if k in _FIELD_TITLES}
            if row.get("id"):
                item_node_ids[key] = row["id"]
    return True, (issues, project_items, item_node_ids)


# `gh issue view --json projectItems` carries only the project title and Status, not the
# State/Phase/Run/Cost/Gate values `plan` compares, so the item's own row is one GraphQL read.
_ITEM_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
    "issue(number:$number){projectItems(first:20){nodes{id project{number owner{"
    "... on Organization{login} ... on User{login}}} fieldValues(first:30){nodes{"
    "... on ProjectV2ItemFieldTextValue{text field{... on ProjectV2FieldCommon{name}}} "
    "... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}}}}}}}}"
)


def project_item_from_response(data: dict, project: str) -> tuple[dict, str]:
    """`(fields, node_id)` of the issue's item in `project` (`owner/number`), or `({}, "")`.
    Fields are keyed by the titles in `_FIELD_TITLES`."""
    owner, _, number = project.partition("/")
    issue = ((data.get("data") or {}).get("repository") or {}).get("issue") or {}
    for node in (issue.get("projectItems") or {}).get("nodes") or []:
        meta = node.get("project") or {}
        if str(meta.get("number")) != number or (meta.get("owner") or {}).get("login", "").lower() != owner.lower():
            continue
        values = [v for v in (node.get("fieldValues") or {}).get("nodes") or [] if v]
        fields = {_FIELD_TITLES[title.lower()]: v.get("text", v.get("name", ""))
                  for v in values if (title := (v.get("field") or {}).get("name", "")).lower() in _FIELD_TITLES}
        return fields, node.get("id", "")
    return {}, ""


def existing_item(run, repo: str, issue: str | None, project: str | None):
    """`existing`'s result shape for the one issue `repo#issue`: one `gh issue view`, plus one
    GraphQL read of its project item when `project` is set. Never lists issues or the project.
    An item with no issue yet, or an issue without the label, reads as absent."""
    if not issue or not repo:
        return True, ({}, {}, {})
    view = run(["gh", "issue", "view", issue, "--repo", repo, "--json", "number,title,body,state,labels"],
               capture_output=True, text=True)
    if view.returncode != 0:
        return False, view.stderr.strip() or view.stdout.strip()
    row = json.loads(view.stdout or "{}")
    if _LABEL not in [label.get("name") for label in row.get("labels") or []]:
        return True, ({}, {}, {})
    issues = {issue: {"title": row.get("title", ""), "body": row.get("body", ""), "state": row.get("state", "")}}
    if project is None:
        return True, (issues, {}, {})
    owner, _, name = repo.partition("/")
    item = run(["gh", "api", "graphql", "-f", f"query={_ITEM_QUERY}", "-f", f"owner={owner}",
                "-f", f"name={name}", "-F", f"number={issue}"], capture_output=True, text=True)
    if item.returncode != 0:
        return False, item.stderr.strip() or item.stdout.strip()
    data = json.loads(item.stdout or "{}")
    # A query the schema rejects can still exit 0 with only `errors`; that must fail loudly, not read as "no item".
    if data.get("errors") or "data" not in data:
        return False, "; ".join(e.get("message", "") for e in data.get("errors") or []) or "graphql: no data"
    fields, node_id = project_item_from_response(data, project)
    if not node_id:
        return True, (issues, {}, {})
    return True, (issues, {issue: fields}, {issue: node_id})


def find_project(run, owner: str, name: str = "Coxswain"):
    """`(True, number)` when a project titled `name` exists under `owner`,
    `(True, None)` when the listing succeeded but none matched, `(False,
    detail)` when the `gh project list` call itself failed."""
    result = run(["gh", "project", "list", "--owner", owner, "--format", "json"],
                 capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    parsed = json.loads(result.stdout or "[]")
    projects = parsed.get("projects", []) if isinstance(parsed, dict) else parsed
    match = next((p for p in projects if p.get("title") == name), None)
    return True, (int(match["number"]) if match is not None else None)


def create_project(run, owner: str, name: str = "Coxswain"):
    """`(True, number)` for a freshly created project, `(False, detail)`
    when `gh project create` fails."""
    result = run(["gh", "project", "create", "--owner", owner, "--title", name, "--format", "json"],
                 capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, int(json.loads(result.stdout)["number"])


def _find_item_file(root: Path, item_id: str):
    candidates = list((root / "intake").glob("*.md")) if (root / "intake").is_dir() else []
    candidates += list(root.glob("work/*/*/*.md"))
    for path in candidates:
        fields, _ = route.parse_frontmatter(path.read_text())
        if fields.get("id", path.stem) == item_id:
            return path
    return None


def _rewrite_issue_line(text: str, issue: str) -> str:
    """Pure: `text` with only its `issue:` frontmatter line replaced, or
    inserted just before the closing `---` when it never had one."""
    lines = text.splitlines(keepends=True)
    new_line = f"issue: {issue}\n"
    for i, line in enumerate(lines):
        if line.startswith("issue:"):
            return "".join(lines[:i] + [new_line] + lines[i + 1:])
        if line.rstrip("\n") == "---" and i > 0:
            return "".join(lines[:i] + [new_line] + lines[i:])
    return text


def _writeback(root: Path, item_id: str, issue: str) -> None:
    if not issue:
        return
    path = _find_item_file(root, item_id)
    if path is None:
        return
    path.write_text(_rewrite_issue_line(path.read_text(), issue))


def _resolved_issue(step: dict, ctx: dict):
    if step["issue"] is not None:
        return step["issue"]
    return ctx["just_created"]["issue"] if ctx["just_created"] else None


def _step_repo(issue, ctx: dict) -> str:
    just_created = ctx["just_created"]
    if just_created and just_created["issue"] == issue:
        return just_created["repo"]
    return ctx["repo_by_issue"].get(issue, "")


def _execute_step(step: dict, run, ctx: dict) -> tuple[bool, str]:
    """One step through `gh`, `(ok, detail)` — `land.py`'s own edge shape."""
    kind = step["kind"]
    if kind == "refuse":
        return False, step["detail"]
    if kind == "issue_create":
        result = run(["gh", "issue", "create", "--repo", step["repo"], "--title", step["title"],
                      "--body", step["body"], "--label", step["label"]], capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        issue = result.stdout.strip().rsplit("/", 1)[-1]
        if not issue:
            return False, "gh issue create returned no issue url"
        ctx["just_created"] = {"issue": issue, "repo": step["repo"]}
        # Recorded at once, so a later failed step cannot leave the issue unknown and a rerun duplicate it.
        _writeback(ctx["root"], step["item_id"], issue)
        return True, issue
    if kind == "issue_edit":
        repo = _step_repo(step["issue"], ctx)
        result = run(["gh", "issue", "edit", step["issue"], "--repo", repo, "--title", step["title"],
                      "--body", step["body"]], capture_output=True, text=True)
        return result.returncode == 0, (result.stdout.strip() or result.stderr.strip())
    if kind == "issue_close":
        result = run(["gh", "issue", "close", step["issue"], "--repo", _step_repo(step["issue"], ctx)],
                     capture_output=True, text=True)
        return result.returncode == 0, (result.stdout.strip() or result.stderr.strip())
    if kind == "project_add":
        issue = _resolved_issue(step, ctx)
        repo = _step_repo(issue, ctx)
        owner, _, number = ctx["project"].partition("/")
        result = run(["gh", "project", "item-add", number, "--owner", owner, "--url",
                      f"https://github.com/{repo}/issues/{issue}", "--format", "json"],
                     capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        node_id = json.loads(result.stdout or "{}").get("id", "")
        if node_id:
            ctx["item_ids"][issue] = node_id
        return True, issue
    if kind == "project_set":
        issue = _resolved_issue(step, ctx)
        item_id = ctx["item_ids"].get(issue, "")
        field_id = ctx["field_ids"].get(step["field"], "")
        if not item_id or not field_id:
            return False, f"no project item/field id known for {step['field']!r} on issue {issue}"
        if step["field"] == "State":
            option_id = ctx["option_ids"].get(("State", step["value"]), "")
            if not option_id:
                return False, f"no option id known for State={step['value']!r}"
            result = run(["gh", "api", "graphql", "-f", f"query={_STATE_MUTATION}",
                          "-f", f"project={ctx['project_node_id']}", "-f", f"item={item_id}",
                          "-f", f"field={field_id}", "-f", f"option={option_id}"],
                         capture_output=True, text=True)
        else:
            # `gh` refuses an empty `--text` ("no changes to make"); an emptied field is cleared.
            value = ["--text", str(step["value"])] if step["value"] != "" else ["--clear"]
            result = run(["gh", "project", "item-edit", "--id", item_id, "--project-id",
                          ctx["project_node_id"], "--field-id", field_id, *value],
                         capture_output=True, text=True)
        return result.returncode == 0, (result.stdout.strip() or result.stderr.strip())
    if kind == "writeback":
        issue = _resolved_issue(step, ctx)
        if not issue:
            return False, "no issue to write back"
        _writeback(ctx["root"], step["item_id"], issue)
        return True, issue
    return False, f"unknown step kind {kind!r}"


def _project_meta(run, project: str):
    """`(True, (project_node_id, field_ids, option_ids))`, or `(False,
    detail)` on the first failed call. Every id `gh project item-edit` and
    the GraphQL field-value mutation take is a node id, not the project
    number or the issue number, so this is resolved once per `execute` call
    rather than guessed from data the plan already carries."""
    owner, _, number = project.partition("/")
    view = run(["gh", "project", "view", number, "--owner", owner, "--format", "json"],
               capture_output=True, text=True)
    if view.returncode != 0:
        return False, view.stderr.strip() or view.stdout.strip()
    fields = run(["gh", "project", "field-list", number, "--owner", owner, "--format", "json"],
                 capture_output=True, text=True)
    if fields.returncode != 0:
        return False, fields.stderr.strip() or fields.stdout.strip()
    field_ids: dict[str, str] = {}
    option_ids: dict[tuple[str, str], str] = {}
    for field in json.loads(fields.stdout or "{}").get("fields", []):
        field_ids[field["name"]] = field["id"]
        for option in field.get("options") or []:
            option_ids[(field["name"], option["name"])] = option["id"]
    return True, (json.loads(view.stdout or "{}").get("id", ""), field_ids, option_ids)


def execute(steps: list[dict], run, project: str, workspace, items=(), item_node_ids=None):
    """Walk `steps` through `gh`: one `(kind, ok, detail)` per step. Stops
    after the first failed `gh` call so a later step never builds on state a
    failure left half-written — `land.py`'s own stop-on-failure shape. A
    `refuse` step (the planner's own "this item's state made no sense")
    never calls `gh` and never stops the rest of the run, since it touched
    nothing to begin with."""
    needs_meta = any(s["kind"] in ("project_add", "project_set") for s in steps)
    if needs_meta and project:
        ok, meta = _project_meta(run, project)
        if not ok:
            return [("project_meta", False, meta)]
        project_node_id, field_ids, option_ids = meta
    else:
        project_node_id, field_ids, option_ids = "", {}, {}
    ctx = {
        "project": project,
        "root": Path(workspace),
        "repo_by_issue": {item.issue: item.repo for item in items if item.issue},
        "item_ids": dict(item_node_ids or {}),
        "just_created": None,
        "project_node_id": project_node_id,
        "field_ids": field_ids,
        "option_ids": option_ids,
    }
    log = []
    for step in steps:
        ok, detail = _execute_step(step, run, ctx)
        log.append((step["kind"], ok, detail))
        if not ok and step["kind"] != "refuse":
            break
    return log
