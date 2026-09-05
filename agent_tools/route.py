"""Pure core for the routing layer: profile parsing and naming. No file
reads, no env access — every function here takes plain arguments and
returns plain values."""

from __future__ import annotations

import os
import re

__all__ = [
    "ProfileError",
    "child_env",
    "context_document",
    "harness_argv",
    "initiative_files",
    "initiative_summaries",
    "intake_entries",
    "intake_file",
    "next_run_id",
    "parse_frontmatter",
    "parse_pid",
    "parse_profile",
    "render_context",
    "render_status",
    "run_entries",
    "slugify",
    "status_entries",
    "status_rows",
    "work_item",
]

_PROFILE_FIELDS = (
    "team",
    "cartridges_dir",
    "skills_roots",
    "provider_profile",
    "harness_dir",
    "workspace_dir",
    "assume",
)

_KNOWN_KEYS = {
    "team",
    "cartridges_dir",
    "skills_roots",
    "provider_profile",
    "harness_dir",
    "workspace_dir",
    "assume",
}


class ProfileError(Exception):
    """A profile file line is nested, unknown, or otherwise unparsable."""


def parse_profile(text: str) -> dict:
    """Parse the flat `key: scalar` / `key: [a, b]` YAML subset in spec §1.

    A nested key (leading whitespace) or a key outside the known set raises
    ProfileError naming the offending line (number + text). `assume`
    defaults to 'a' when absent.
    """
    result: dict = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\n")
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        if line != line.lstrip():
            raise ProfileError(f"line {lineno}: {raw_line}")
        # An inline `#` (preceded by whitespace, per spec §1's own sample
        # `assume: a          # gate answer ...`) starts a trailing comment;
        # strip it before splitting key/value so it never lands in a value.
        comment = re.search(r"(?<=\s)#", line)
        content = line[: comment.start()].rstrip() if comment else line
        if ":" not in content:
            raise ProfileError(f"line {lineno}: {raw_line}")
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        if key not in _KNOWN_KEYS:
            raise ProfileError(f"line {lineno}: {raw_line}")
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            items = [item.strip() for item in inner.split(",")] if inner else []
            result[key] = items
        else:
            result[key] = value
    # An `assume:` with no value is treated the same as an absent key, not
    # as a literal empty string, so it never poisons the harness argv.
    if result.get("assume") == "":
        result.pop("assume")
    result.setdefault("assume", "a")
    return result


def slugify(title: str) -> str:
    """Lower-case, non-alphanumerics collapsed to '-', truncated to 48 chars.

    A title with no alphanumeric characters (all punctuation/symbols)
    slugifies to the empty string; a caller that uses the result as a path
    segment (spec §3, `work/<slug>/...`) must guard against that itself —
    this pure core does not refuse or substitute.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return slug[:48].strip("-")


def next_run_id(existing_names, prefix: str) -> str:
    """Return `<prefix>-<n>` for the smallest n not already used by a name
    in existing_names with that prefix.

    Spec §4/§5 fill `existing_names` from a real `runs_dir` listing, whose
    entries are `<run-id>.log` and `<run-id>.pid`, not bare ids — so the
    pattern must tolerate an optional trailing extension. Matching bare ids
    only would let `next_run_id` hand back an id already in use, appending
    to a live run's log and clobbering its pidfile.
    """
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)(?:\.\w+)?$")
    taken = set()
    for name in existing_names:
        match = pattern.match(name)
        if match:
            taken.add(int(match.group(1)))
    n = 1
    while n in taken:
        n += 1
    return f"{prefix}-{n}"


class _Raw(str):
    """Marks a frontmatter value this module wrote itself as a literal YAML
    fragment (`needs: []`, `surfaces: []`) that must be emitted verbatim.

    Never wrap a caller-supplied string in this: a value's *shape* is not
    evidence of its meaning. An earlier version of `_yaml_scalar` treated
    any bracketed value as a raw list, which meant a title like
    `[draft] ship it` was emitted bare as `title: [draft] ship it` — a real
    YAML reader parses that as a flow sequence, not the string it looks
    like. Only the caller who wrote the literal can know it is one; that
    is what wrapping it in `_Raw` at the call site records.
    """


def _yaml_scalar(value: str) -> str:
    """Quote a frontmatter value that would otherwise read as something
    other than the plain string it is.

    Wrong belief this guards against: that emitting `key: {value}` verbatim
    is safe because a title or repo is "just text". A title with a colon
    (this very task's own title, `route.py: initiative_files`) or a `#`
    turns `title: route.py: initiative_files` into a line a real YAML
    reader parses as a nested mapping or a truncated comment, not the
    string it looks like. `_Raw` values pass through unquoted; every other
    value is a scalar regardless of what it looks like.
    """
    if isinstance(value, _Raw):
        return str(value)
    starts_like_yaml_flow = value.startswith("[") or value.startswith("{")
    # `'` is quoted for the same reason as `"`: a value that already looks
    # quoted must not be handed to a YAML reader bare. This half has no
    # round-trip test below. `parse_frontmatter` has no single-quoted value
    # form, so a title starting with `'` parses as the bare string whether
    # or not this line quotes it, and a test that failed on its removal
    # would have to assert the writer's output shape, which this task
    # rules out.
    starts_like_a_quote = value.startswith('"') or value.startswith("'")
    if (
        value == ""
        or ":" in value
        or "#" in value
        or value != value.strip()
        or starts_like_yaml_flow
        or starts_like_a_quote
    ):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _frontmatter(fields, body: str) -> str:
    """Render `---`-delimited frontmatter followed by a body, spec §3.

    `fields` is an ordered iterable of (key, value) pairs; each value is
    quoted by `_yaml_scalar` when left bare it would change meaning (a
    colon, a `#`, surrounding whitespace, or emptiness), never otherwise.
    """
    header = "\n".join(f"{key}: {_yaml_scalar(value)}" for key, value in fields)
    return f"---\n{header}\n---\n\n{body}\n"


def _slug_or_raise(title: str) -> str:
    """`slugify`'s own docstring hands the empty-slug case to any caller
    that uses the result as a path segment (spec §3, `work/<slug>/...`).
    A title of pure punctuation would otherwise slugify to `""`, and
    `work/{slug}/...` would collapse to `work/initiative.md` — a write to
    the top of the work store, past spec §3's existing-path refusal, since
    that path is genuinely new. This is the guard slugify asked for.
    """
    slug = slugify(title)
    if not slug:
        raise ValueError(f"title {title!r} has no alphanumeric characters to slugify")
    return slug


def initiative_files(title: str, body: str, repo: str, phase: str = "build") -> dict:
    """Content for a one-task initiative (spec §3, `route file` without
    `--intake`): `work/<slug>/initiative.md` and
    `work/<slug>/<phase>/<slug>.md`, keyed by path relative to the work
    store. `body` falls back to `title` when empty. No filesystem writes
    happen here — the caller applies the mapping.
    """
    slug = _slug_or_raise(title)
    text = body if body else title
    initiative_text = _frontmatter(
        [("id", slug), ("title", title), ("repo", repo)], text
    )
    task_text = _frontmatter(
        [
            ("id", slug),
            ("phase", phase),
            ("state", "ready"),
            ("needs", _Raw("[]")),
            ("surfaces", _Raw("[]")),
            ("title", title),
        ],
        text,
    )
    return {
        f"work/{slug}/initiative.md": initiative_text,
        f"work/{slug}/{phase}/{slug}.md": task_text,
    }


def intake_file(title: str, body: str, repo: str, date: str) -> dict:
    """Content for `route file --intake` (spec §3):
    `intake/<date>-<slug>.md`, same frontmatter shape as the initiative
    file. `date` arrives as a string (e.g. `2026-09-03`) so this stays
    pure — no clock reads here.
    """
    slug = _slug_or_raise(title)
    text = body if body else title
    file_text = _frontmatter([("id", slug), ("title", title), ("repo", repo)], text)
    return {f"intake/{date}-{slug}.md": file_text}


def harness_argv(profile: dict, graph: str, run_id: str, **needs) -> list:
    """Build the harness command line, spec §4, from a parsed `profile`
    and the graph-specific `needs` (`initiative`/`repo` for `epic`,
    `idea`/`initiative_id` for `decompose`). Pure: no Popen, no env reads.
    """
    harness_dir = profile["harness_dir"]
    workspace_dir = profile["workspace_dir"]
    argv = [
        f"{harness_dir}/.venv/bin/python",
        f"{harness_dir}/shell.py",
        graph,
        "--team",
        profile["team"],
        "--cartridges-dir",
        profile["cartridges_dir"],
    ]
    for root in profile.get("skills_roots", []):
        argv += ["--skills-root", root]
    argv += [
        "--provider-profile",
        profile["provider_profile"],
        "--runs-dir",
        f"{workspace_dir}/runs",
        "--assume",
        profile["assume"],
        "--run-id",
        run_id,
    ]
    if graph == "epic":
        argv += ["--initiative", needs["initiative"], "--repo", needs["repo"]]
        if needs.get("fix_attempts") is not None:
            argv += ["--fix-attempts", str(needs["fix_attempts"])]
    elif graph == "decompose":
        argv += ["--idea", needs["idea"], "--initiative-id", needs["initiative_id"]]
    elif graph == "cos":
        # `_KNOWN_KEYS` carries no `max_parallel` field, so a profile can
        # never supply one; the bound is the literal default until the
        # profile schema grows a key for it.
        argv += ["--max-parallel", "3"]
    argv += ["--workdir", workspace_dir]
    return argv


def _alive_runs(runs: list) -> list:
    """The subset of `runs` still in flight — spec §2's "in flight" count
    and roster both come from this, not from every entry with a pidfile."""
    return [r for r in runs if r["alive"]]


def render_context(profile_or_none, intake, runs, initiatives) -> str:
    """The human-readable layout `agent-tools route context` prints, spec
    §2. `profile_or_none` is a parsed profile dict or None; `intake`,
    `runs`, `initiatives` are already-gathered lists — this function lists
    a directory or reads a pidfile for none of it, that is the CLI's job.

    `intake` items are `{"id", "title"}`; `runs` are
    `{"id", "pid", "alive", "started"}`; `initiatives` are
    `{"id", "phase", "ready"}` (ready task count) — the exact shapes spec
    §2 names for the renderer's inputs.
    """
    if profile_or_none is None:
        return (
            "routing: no profile at ~/.config/agent-tools/profile.yaml; "
            "the harness is not configured on this machine"
        )
    team = profile_or_none.get("team", "")
    lines = [
        f"routing: team {team}; work requests go through the route-work "
        "skill, questions stay inline"
    ]
    if intake:
        titles = ", ".join(f'"{item["title"]}"' for item in intake)
        lines.append(f"intake: {len(intake)} queued — {titles}")
    else:
        lines.append("intake: 0 queued")
    live = _alive_runs(runs)
    if live:
        described = ", ".join(
            f'{r["id"]} (pid {r["pid"]}, since {r["started"]})' for r in live
        )
        lines.append(f"runs: {len(live)} in flight — {described}")
    else:
        lines.append("runs: 0 in flight")
    if initiatives:
        described = ", ".join(
            f'{i["id"]} ({i["ready"]} tasks ready in phase {i["phase"]})'
            for i in initiatives
        )
        lines.append(f"ready: {described}")
    else:
        lines.append("ready: none")
    return "\n".join(lines)


def context_document(profile_or_none, intake, runs, initiatives) -> dict:
    """The `--json` shape for `agent-tools route context`, spec §2: the
    same facts as `render_context`, keyed on exactly the profile fields
    `parse_profile` produces so the no-profile case (every profile field
    None) renders cleanly rather than omitting keys a caller would have
    to guard for.
    """
    doc = {
        field: (profile_or_none.get(field) if profile_or_none else None)
        for field in _PROFILE_FIELDS
    }
    doc["intake"] = intake
    doc["runs"] = runs
    doc["live"] = len(_alive_runs(runs))
    doc["initiatives"] = initiatives
    return doc


def status_rows(entries) -> list:
    """Format the rows `agent-tools route status` prints, spec §5, from a
    pre-gathered list of run entries. Each entry is
    `{"id", "pid", "alive", "started", "quarantined", "reused", "summary",
    "usage"}` — `pid`/`alive`/`started` from the CLI's own pidfile read,
    the rest already produced by `epic.summarize_log` on the run's log.
    `pid` is None for a run with no pidfile at all, which this renders as
    a distinct "no pidfile" state rather than a crash. Order is preserved
    from `entries`; no pidfile is read and `epic.alive` is not called here.
    """
    rows = []
    for entry in entries:
        pid = entry.get("pid")
        if pid is None:
            state = "no pidfile"
        elif entry.get("alive"):
            state = "alive"
        else:
            state = "exited"
        rows.append(
            {
                "id": entry["id"],
                "pid": pid,
                "state": state,
                "started": entry.get("started"),
                "quarantined": entry.get("quarantined", []),
                "reused": entry.get("reused", []),
                "summary": entry.get("summary"),
                "usage": entry.get("usage"),
            }
        )
    return rows


def _status_line(row: dict) -> str:
    head = (
        f"{row['id']}: {row['state']}"
        if row["pid"] is None
        else f"{row['id']}: {row['state']} (pid {row['pid']}, started {row['started']})"
    )
    tails = (
        ["; ".join(row[key]) for key in ("quarantined", "reused") if row.get(key)]
        + [row[key] for key in ("summary", "usage") if row.get(key)]
    )
    return " ".join([head, *tails])


def render_status(rows: list) -> str:
    """The human-readable text `agent-tools route status` prints, spec §5,
    from `status_rows`' output. One line per row: a run with no pidfile
    states only its id and state, since `pid` and `started` are both
    `None` for that row and printing them would show the word "None"
    twice for no reason. `quarantined`, `reused`, `summary` and `usage`
    are appended only when the row carries them, so a quiet run stays
    one line.
    """
    return "\n".join(_status_line(row) for row in rows)


def status_entries(runs: list, summaries: dict) -> list:
    """The entries `status_rows` expects, assembled from what the CLI has
    already read: `runs` is `run_entries`' output (one `{"id", "pid",
    "alive", "started"}` per id with a pidfile), `summaries` is `id ->
    epic.summarize_log`'s output for whatever log text the CLI found for
    that id, `{}` where there was none. One entry per id in the union of
    both, sorted by id; an id with a log but no pidfile gets the pid-less
    shape `run_entries` would have given it: `pid` `None`, `alive`
    `False`, `started` `None`.
    """
    by_id = {row["id"]: row for row in runs}
    no_pidfile = {"pid": None, "alive": False, "started": None}
    return [
        {"id": run_id, **by_id.get(run_id, no_pidfile), **summaries.get(run_id, {})}
        for run_id in sorted(set(by_id) | set(summaries))
    ]


def child_env(environ: dict, *, harness_dir: str = "", repo: str = "", trace_dir: str = "") -> dict:
    """The harness child's environment, spec §4: `environ` with
    `<repo>/.venv/bin` and `<harness_dir>/.venv/bin` prepended to `PATH`
    when given, in that order. Every other key is untouched; `environ`
    itself is never mutated. When `trace_dir` is given, `AGENT_GRAPHS_TRACE_DIR`
    is set to it, replacing any value already in `environ`; when omitted, the
    key is left exactly as it was.
    """
    env = dict(environ)
    prefixes = [p for p in (f"{repo}/.venv/bin" if repo else "",
                             f"{harness_dir}/.venv/bin" if harness_dir else "") if p]
    if prefixes:
        rest = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(prefixes + ([rest] if rest else []))
    if trace_dir:
        env["AGENT_GRAPHS_TRACE_DIR"] = trace_dir
    return env


def _unquote(raw: str) -> str:
    """Reverse `_yaml_scalar`'s double-quoted branch: `raw` is the quoted
    scalar including its outer quotes. Undo in the opposite order to the
    encode (quotes first, then backslashes) — encoding doubles backslashes
    before it escapes quotes, so decoding must collapse the quote escape
    before the backslash escape or a literal `\\"` collapses to the wrong
    character.
    """
    inner = raw[1:-1]
    quotes_undone = inner.replace('\\"', '"')
    return quotes_undone.replace("\\\\", "\\")


def _parse_list(raw: str) -> list:
    """Reverse the flat-list branch: `raw` is `[...]`, comma-separated bare
    items, `[]` for empty. Not a YAML parser — no quoted or nested items.
    """
    inner = raw[1:-1].strip()
    return [] if inner == "" else [item.strip() for item in inner.split(",")]


def _parse_field(line: str):
    """One `key: value` header line back to `(key, value)`, dispatching on
    the value's first character the same three ways `_yaml_scalar` and the
    list literals write them. A line with no `": "` separator — a stray
    comment, a block-list item, a hand edit — is not a line this module
    ever wrote, so it is not a field: `None` tells the caller to drop it
    rather than raise, which is what "never raises" requires of every line
    in the fence, not just the well-formed ones.
    """
    parts = line.split(": ", 1)
    if len(parts) != 2:
        return None
    key, raw = parts
    if raw.startswith('"'):
        return key, _unquote(raw)
    if raw.startswith("["):
        return key, _parse_list(raw)
    return key, raw


def parse_frontmatter(text: str) -> tuple:
    """The inverse of `_frontmatter`: `(fields, body)` from `---`-delimited
    text of the shape `_frontmatter` writes. `text` with no leading `---`
    line, or with an opening `---` and no closing one, comes back as
    `({}, text)` unchanged — this never raises. This round-trips only the
    three value shapes `_yaml_scalar` and the list literals produce; it is
    not a general YAML parser and does not try to be.
    """
    opening = "---\n"
    if not text.startswith(opening):
        return {}, text
    after_opening = text[len(opening):]
    closing = "\n---\n"
    close_index = after_opening.find(closing)
    if close_index == -1:
        return {}, text
    header_block = after_opening[:close_index]
    after_closing = after_opening[close_index + len(closing):]
    header_lines = [line for line in header_block.split("\n") if line]
    parsed_fields = [_parse_field(line) for line in header_lines]
    fields = dict(field for field in parsed_fields if field is not None)
    with_leading_blank_stripped = (
        after_closing[1:] if after_closing.startswith("\n") else after_closing
    )
    body = (
        with_leading_blank_stripped[:-1]
        if with_leading_blank_stripped.endswith("\n")
        else with_leading_blank_stripped
    )
    return fields, body


def _intake_entry(filename: str, text: str) -> dict:
    fields, body = parse_frontmatter(text)
    stem = os.path.splitext(os.path.basename(filename))[0]
    entry_id = fields.get("id", stem)
    body_lines = [line.strip() for line in body.split("\n") if line.strip()]
    first_body_line = body_lines[0] if body_lines else entry_id
    return {"id": entry_id, "title": fields.get("title", first_body_line)}


def intake_entries(files: dict) -> list:
    """Rows for the intake queue, the `intake` input `render_context` and
    `context_document` already accept: one `{"id", "title"}` per file in
    `files` (filename -> text, as the caller listed the intake dir), sorted
    by filename. A filename under a `consumed/` prefix has already been
    handled and is skipped. `id` comes from frontmatter's `id` field, or the
    filename's stem when frontmatter has none; `title` comes from
    frontmatter's `title` field, or the first non-empty body line, or `id`
    when the body is empty too.
    """
    return [
        _intake_entry(filename, files[filename])
        for filename in sorted(files)
        if not filename.startswith("consumed/")
    ]


def _run_entry(run_id: str, pid_text: str, alive: dict, started: dict) -> dict:
    stripped = pid_text.strip()
    pid = int(stripped) if stripped.isdigit() else None
    return {
        "id": run_id,
        "pid": pid,
        "alive": alive.get(run_id, False) if pid is not None else False,
        "started": started.get(run_id),
    }


def run_entries(pids: dict, alive: dict, started: dict) -> list:
    """Rows for the runs list, the `runs` input `render_context` and
    `context_document` already accept: one `{"id", "pid", "alive",
    "started"}` per run id in `pids` (pidfile text the caller already read),
    sorted by id. A pidfile whose text is not a plain integer — a partial
    write, a stray hand edit — is treated as unreadable: `pid` comes back
    `None` and `alive` comes back `False` regardless of what the caller
    passed in `alive` for that id.
    """
    return [_run_entry(run_id, pids[run_id], alive, started) for run_id in sorted(pids)]


def _ready_unblocked(item: dict, done_ids: set) -> bool:
    return item["state"] == "ready" and all(need in done_ids for need in item["needs"])


def _initiative_summary(initiative_id: str, own_items: list):
    done_ids = {item["id"] for item in own_items if item["state"] == "done"}
    ready_phases = sorted(
        {item["phase"] for item in own_items if _ready_unblocked(item, done_ids)}
    )
    if not ready_phases:
        return None
    phase = ready_phases[0]
    ready_count = sum(
        1
        for item in own_items
        if item["phase"] == phase and _ready_unblocked(item, done_ids)
    )
    return {"id": initiative_id, "phase": phase, "ready": ready_count}


def initiative_summaries(items: list) -> list:
    """One row per initiative with a task ready to run, the `initiatives`
    input `render_context` and `context_document` already accept: `items`
    are work items with at least `id`, `initiative`, `phase`, `state`,
    `needs`, each parsed with `parse_frontmatter` by the caller. Each row is
    `{"id": <initiative>, "phase": <sorted-first phase holding a ready,
    unblocked task>, "ready": <count of such tasks in that phase>}`.
    "Unblocked" means every id in `needs` names a task, anywhere in the same
    initiative, whose state is "done". An initiative with no ready,
    unblocked task is omitted. Sorted by initiative id.
    """
    initiative_ids = sorted({item["initiative"] for item in items})
    summaries = [
        _initiative_summary(
            initiative_id,
            [item for item in items if item["initiative"] == initiative_id],
        )
        for initiative_id in initiative_ids
    ]
    return [summary for summary in summaries if summary is not None]


def parse_pid(text: str) -> int | None:
    """A pidfile's text to a pid, or `None` when it isn't one: stripped,
    digit-only text becomes an int; anything else — empty, garbage, a
    partial write — is not a pid this module will hand to `os.kill`.
    """
    stripped = text.strip()
    return int(stripped) if stripped.isdigit() else None


def work_item(fields: dict, *, initiative: str, phase_dir: str, stem: str) -> dict:
    """A work item row for `initiative_summaries`, filled in from the path
    the caller read it from wherever frontmatter left a gap: `id` falls
    back to `stem`, `phase` to `phase_dir`, `state` to `"todo"`, `needs` to
    `[]`. `initiative` always comes from the argument — the directory name
    is the initiative, never a frontmatter claim to the contrary.
    """
    return {
        **fields,
        "id": fields.get("id", stem),
        "initiative": initiative,
        "phase": fields.get("phase", phase_dir),
        "state": fields.get("state", "todo"),
        "needs": fields.get("needs", []),
    }
