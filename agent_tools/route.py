"""Pure core for the routing layer: profile parsing and naming. No file
reads, no env access — every function here takes plain arguments and
returns plain values."""

from __future__ import annotations

import os
import re

__all__ = [
    "ProfileError",
    "parse_profile",
    "slugify",
    "next_run_id",
    "initiative_files",
    "intake_file",
    "harness_argv",
    "child_env",
    "render_context",
    "context_document",
    "status_rows",
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
    slug = slug[:48].strip("-")
    return slug


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
    if (
        value == ""
        or ":" in value
        or "#" in value
        or value != value.strip()
        or starts_like_yaml_flow
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
    elif graph == "decompose":
        argv += ["--idea", needs["idea"], "--initiative-id", needs["initiative_id"]]
    argv += ["--workdir", workspace_dir]
    return argv


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
    if runs:
        described = ", ".join(
            f'{r["id"]} (pid {r["pid"]}, since {r["started"]})' for r in runs
        )
        lines.append(f"runs: {len(runs)} in flight — {described}")
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


def child_env(environ: dict, *, harness_dir: str = "", repo: str = "") -> dict:
    """The harness child's environment, spec §4: `environ` with
    `<repo>/.venv/bin` and `<harness_dir>/.venv/bin` prepended to `PATH`
    when given, in that order. Every other key is untouched; `environ`
    itself is never mutated.
    """
    env = dict(environ)
    prefixes = [p for p in (f"{repo}/.venv/bin" if repo else "",
                             f"{harness_dir}/.venv/bin" if harness_dir else "") if p]
    if prefixes:
        rest = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(prefixes + ([rest] if rest else []))
    return env
