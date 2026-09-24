"""The pure planner over coxswain's lockstep release: what `cox release
<version> --dry-run` prints, and the argv `cox release <version>` runs
through an injected runner. No filesystem, subprocess or clock here —
`cli.py` gathers the existing tags, resolves checkout directories, and
runs the git commands at the edge."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import yaml

from agent_tools.release_check_index import index_section

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$")


def _parse_semver(version: str) -> tuple[int, int, int, int | None] | None:
    """`(major, minor, patch, beta_n)` with `beta_n` `None` for a release,
    or `None` when `version` does not match `X.Y.Z` or `X.Y.Z-beta.N`."""
    m = _VERSION_RE.match(version)
    if not m:
        return None
    major, minor, patch, beta = m.groups()
    return (int(major), int(minor), int(patch), int(beta) if beta is not None else None)


def _sort_key(parsed: tuple[int, int, int, int | None]) -> tuple:
    """A beta sorts below its own release: `None` (release) must compare
    greater than any integer beta number at the same major.minor.patch."""
    major, minor, patch, beta = parsed
    return (major, minor, patch, 0 if beta is not None else 1, beta if beta is not None else 0)


def _refuse(component: str | None, detail: str) -> list[dict]:
    return [{"kind": "refuse", "component": component, "detail": detail}]


_VERSIONS_LABEL_RE = re.compile(r"^(\S+) pyproject\.toml is (\S+), manifest wants (\S+)")
_PERFORMS_BUMP_SUFFIX_RE = re.compile(r"\s*\(cox dev release [^)]*\)$")


def _drift_line(d, will_bump: bool = False) -> str:
    a = f"{d.a_file}:{d.a_line}" if d.a_line is not None else d.a_file
    b = f"{d.b_file}:{d.b_line}" if d.b_line is not None else d.b_file
    correction = d.correction
    if d.check == "versions":
        correction = _PERFORMS_BUMP_SUFFIX_RE.sub("", correction)
        correction += " — the release bumps it" if will_bump else " — bump it by hand"
    return f"{d.check}: {a} <-> {b} — {correction}"


def _versions_drift_is_planned(d, plan_steps: Sequence[dict]) -> bool:
    """True when `plan_steps` already carries the bump that resolves `d`: a
    `bump_pyproject` step for the same component landing at the version the
    drift wants, or — when `d` names the umbrella's own pyproject.toml — a
    `bump_manifest` step landing there. `d.correction`'s own wording
    (`"<label> pyproject.toml is <found>, manifest wants <wants>"`, written by
    `release_check.check_versions`) is the only place that names both the
    component and the target version, so it is parsed rather than re-derived."""
    m = _VERSIONS_LABEL_RE.match(d.correction)
    if not m:
        return False
    label, _found, wants = m.groups()
    if label == "umbrella":
        return any(s["kind"] == "bump_manifest" and s.get("to") == wants for s in plan_steps)
    return any(s["kind"] == "bump_pyproject" and s.get("component") == label and s.get("to") == wants
               for s in plan_steps)


def gate(drifts: Sequence, allow_reason: str | None, plan_steps: Sequence[dict] = ()) -> list[dict]:
    """Refuse steps naming each drift, or one note step when `allow_reason`
    says why they stand; empty when `drifts` is empty. A `versions` drift
    still refuses unless `plan_steps` (the same invocation's `release_plan`
    output) already carries the bump that resolves it, in which case it
    becomes a note instead — the plan is about to do exactly what the drift
    asks for. A `versions` drift the plan does not touch, or a pyproject
    ahead of the manifest (no bump step is ever planned for that), still
    refuses."""
    if not drifts:
        return []
    versions = [d for d in drifts if d.check == "versions"]
    other = [d for d in drifts if d.check != "versions"]
    bumped = [d for d in versions if _versions_drift_is_planned(d, plan_steps)]
    blocking = [d for d in versions if d not in bumped]
    blocking_steps = [{"kind": "refuse", "component": d.check, "detail": _drift_line(d)} for d in blocking]
    bumped_steps = [{"kind": "note", "component": d.check, "detail": _drift_line(d, will_bump=True)} for d in bumped]
    if not other:
        return blocking_steps + bumped_steps
    if allow_reason is None:
        return blocking_steps + bumped_steps + [{"kind": "refuse", "component": d.check, "detail": _drift_line(d)} for d in other]
    plural = "" if len(other) == 1 else "s"
    return blocking_steps + bumped_steps + [{"kind": "note", "component": "release-check",
             "detail": f"{allow_reason} ({len(other)} drift{plural} allowed)"}]


def is_maintainer_remote(url: str) -> bool:
    """True when `url` names the ppfenning/coxswain umbrella — the one
    checkout a lockstep release is allowed to run against."""
    return "ppfenning/coxswain" in url


def parse_ls_remote(text: str) -> list[str]:
    """Tag names from `git ls-remote --tags` output, peeled `^{}` refs skipped.
    Pure: the edge fetches the text, this decides what it means."""
    refs = (line.split("\t", 1)[1] for line in text.splitlines() if "\t" in line)
    return [ref[len("refs/tags/"):] for ref in refs if ref.startswith("refs/tags/") and not ref.endswith("^{}")]


def _with_wait_workflows(steps: list[dict]) -> list[dict]:
    """`steps` with a `wait_workflows` step inserted immediately after every
    `tag` or `tag_self` step, naming that same component and tag — the
    release refusing to call itself done until the workflows the tag
    started finish. No other kind gets one."""
    out = []
    for step in steps:
        out.append(step)
        # `rejoin` tags and pushes exactly like `tag`, so it waits the same way.
        if step["kind"] in ("tag", "tag_self", "rejoin"):
            out.append({"kind": "wait_workflows", "component": step["component"], "tag": step["tag"]})
    return out


def declares_tag_trigger(workflow_text: str) -> bool:
    """True when a GitHub Actions workflow's `on: push: tags:` block names
    at least one pattern. PyYAML's default loader reads a bare `on` key as
    the boolean `True` under YAML 1.1, so both spellings are read; a list
    form of `on:` (e.g. `on: [push, pull_request]`) never names a tag."""
    try:
        doc = yaml.safe_load(workflow_text) or {}
    except yaml.YAMLError:
        return False
    if not isinstance(doc, dict):
        return False
    on = doc.get("on", doc.get(True))
    push = on.get("push") if isinstance(on, dict) else None
    return bool(push.get("tags")) if isinstance(push, dict) else False


def _bump_and_land(bump_step: dict, branch: str, body: str) -> list[dict]:
    """`bump_step` landed the way `cox runs land` lands: `push` the branch it
    was committed to, `pr_create`, `wait_checks`, `merge` — the kinds
    `_execute_land_step` (`agent_tools/cli.py`) already knows how to run."""
    component = bump_step["component"]
    title = bump_step["commit_subject"]
    return [bump_step,
            {"kind": "push", "component": component, "branch": branch},
            {"kind": "pr_create", "component": component, "title": title, "body": body},
            {"kind": "wait_checks", "component": component},
            {"kind": "merge", "component": component}]


def _component_github_release_step(name: str, repo: str, tag: str, version: str, umbrella_slug: str | None,
                                    fallback_from: str | None = None) -> dict:
    """A `github_release` step for a component tagged this run: its own
    `## coxswain-<name>` section of the umbrella's release notes, linked back
    to the umbrella's release. `fallback_from` names the tag `cli.py` reports
    "unchanged since" when that section doesn't exist — only a `rejoin`
    component isn't always written one. No `link` at all when `umbrella_slug`
    couldn't be resolved."""
    step = {"kind": "github_release", "component": name, "repo": repo, "tag": tag,
            "title": f"coxswain-{name} {version}", "notes_path": f"docs/releases/{version}.md",
            "heading": f"## coxswain-{name}"}
    if fallback_from is not None:
        step["from"] = fallback_from
    if umbrella_slug is not None:
        step["link"] = f"https://github.com/{umbrella_slug}/releases/tag/{tag}"
    return step


_TAP_REPO = "ppfenning/homebrew-coxswain"
_PYPI_PACKAGE = "coxswain-tools"


TAP_CHECKOUT = "homebrew-coxswain"


def _tap_formula_steps(steps: list[dict], version: str, tools_repository_url: str | None,
                        umbrella_slug: str | None, tap_state: str) -> list[dict]:
    """Nothing unless `steps` tags the tools repo, whose tag push runs the PyPI publish; a `refuse` when the tap checkout is not `clean`."""
    tools_slug = "/".join(tools_repository_url.rstrip("/").split("/")[-2:]) if tools_repository_url else None
    publishing = tools_slug is not None and any(
        (s["kind"] in ("tag", "rejoin") and s["repo"] == tools_slug)
        or (s["kind"] == "tag_self" and umbrella_slug == tools_slug) for s in steps)
    if not publishing:
        return []
    if tap_state != "clean":
        return _refuse("tap", f"tap checkout {_TAP_REPO} is {tap_state}; the formula bump needs a clean checkout")
    pypi_version = re.sub(r"-beta\.(\d+)$", r"b\1", version)
    return [{"kind": "tap_formula_pr", "component": "tap", "repo": _TAP_REPO, "path": "Formula/cox.rb",
             "version": version, "branch": f"release/{version}", "title": f"cox {version}",
             "index_url": f"https://pypi.org/pypi/{_PYPI_PACKAGE}/{pypi_version}/json"}]


def sdist_from_index(payload: Mapping) -> tuple[str, str] | None:
    """The `(url, sha256)` of the sdist in a PyPI `/pypi/<name>/<version>/json` payload, or None when it lists none."""
    return next(((u["url"], u["digests"]["sha256"]) for u in payload.get("urls", [])
                 if u.get("packagetype") == "sdist"), None)


def release_plan(manifest: Mapping, version: str, existing_tags: Mapping[str, list[str] | None],
                  component_versions: Mapping[str, str | None] | None = None,
                  pinned_commits: Mapping[str, int] | None = None,
                  tools_repository_url: str | None = None,
                  tap_state: str = "clean") -> list[dict]:
    """Steps in order: per `repo` component, either a plain `tag` (its
    `component_versions` entry is missing or already at `version`) or a
    `bump_pyproject`-and-land sequence ending in `tag` — unless its manifest
    entry declares `lockstep = false`, in which case it gets one `pinned`
    step naming its own `tag`, or `rejoin` tagging it at `version` when `pinned_commits` shows commits; one `notes`; then
    either a plain `tag_self` or the manifest's own `bump_manifest`-and-land
    sequence ending in `tag_self`. `component_versions` is the version each
    component's own checkout pyproject.toml currently declares — a fact this
    pure function cannot read itself, gathered by the edge the way
    `existing_tags` is. A single `refuse` step, naming the reason, when
    `version` is not valid semver-with-optional-beta, when the tag already
    exists on any component (or on the umbrella, when `existing_tags` carries
    a `"coxswain"` key), or when `version` is strictly less than the
    manifest's current version by semver-with-beta rules.

    `version` equal to the current version is the first cut of the version
    the manifest already declares: nothing is tagged yet, so the plan
    proceeds with no `bump_manifest` step — the manifest already says so.

    A `github_release` step follows every step that actually tags a repo
    this run — a `tag`, a `rejoin`, or the umbrella's `tag_self` — naming
    the `gh release` command `cli.py`'s executor runs through the idempotent
    view-then-create-or-edit check; a `pinned` component gets none, since its
    old release must never be rewritten by a later cut. `tools_repository_url`
    is this package's own `Repository` URL, another fact gathered at the edge
    (from installed package metadata) rather than read here, used only as a
    fallback for the umbrella's slug when the manifest names none.

    A final `tap_formula_pr` step follows only when this plan tags the tools
    repo (`tools_repository_url`) with a `tag`, `rejoin` or `tag_self`: that
    tag push is what publishes to PyPI, so a `lockstep = false` component
    never triggers it. A `refuse` naming the tap replaces it when
    `tap_state` (`clean`, `dirty` or `absent`) says the checkout is unfit."""
    parsed = _parse_semver(version)
    if parsed is None:
        return _refuse(version, f"{version!r} is not a valid version (expected X.Y.Z or X.Y.Z-beta.N)")

    component_versions = component_versions or {}
    new_tag = "v" + version
    components = manifest.get("components", {})
    repo_components = [(name, spec) for name, spec in components.items() if spec.get("repo")]

    # Three states per component: a list of tags, an empty list (reachable, no
    # tags), or None (the remote could not be read). Unknown is not clean: a
    # reused tag is the one thing a release must never risk, so None refuses.
    # A `lockstep = false` component is never tagged here, so its remote's
    # readability and its existing tags are not this release's concern.
    lockstep_components = [(name, spec) for name, spec in repo_components if spec.get("lockstep", True)]
    unknown = sorted(name for name, _ in lockstep_components if existing_tags.get(name) is None)
    if unknown:
        return _refuse(", ".join(unknown), f"tags unknown for {', '.join(unknown)} (remote unreadable); refusing rather than risk reusing {new_tag}")

    collision_sources = [name for name, _ in lockstep_components] + (["coxswain"] if "coxswain" in existing_tags else [])
    colliding = sorted(name for name in collision_sources if new_tag in (existing_tags.get(name) or []))
    if colliding:
        return _refuse(", ".join(colliding), f"tag {new_tag} already exists on {', '.join(colliding)}")

    current = manifest.get("coxswain", {}).get("version")
    current_parsed = _parse_semver(current) if current is not None else None
    umbrella_slug = umbrella_release_slug(manifest, tools_repository_url)
    tag_steps = []
    for name, spec in repo_components:
        if not spec.get("lockstep", True):
            commits = (pinned_commits or {}).get(name) or 0
            if commits:
                rejoin_step = {"kind": "rejoin", "component": name, "repo": spec["repo"], "tag": new_tag,
                               "from": spec["tag"], "commits": commits}
                tag_steps.extend([rejoin_step,
                                   _component_github_release_step(name, spec["repo"], new_tag, version,
                                                                   umbrella_slug, fallback_from=spec["tag"])])
            else:
                tag_steps.append({"kind": "pinned", "component": name, "tag": spec["tag"]})
            continue
        tag_step = {"kind": "tag", "component": name, "repo": spec["repo"], "tag": new_tag}
        gr_step = _component_github_release_step(name, spec["repo"], new_tag, version, umbrella_slug,
                                                   fallback_from=spec["tag"])
        found = component_versions.get(name)
        found_parsed = _parse_semver(found) if found is not None else None
        if found_parsed is None or _sort_key(found_parsed) >= _sort_key(parsed):
            tag_steps.extend([tag_step, gr_step])
            continue
        branch = f"release/{version}"
        subject = f"pyproject: bump to {version} to match the tag"
        bump_step = {"kind": "bump_pyproject", "component": name, "repo": spec["repo"],
                     "branch": branch, "commit_subject": subject, "from": found, "to": version}
        body = f"Bumps {name}'s pyproject.toml version to {version} to match tag {new_tag}."
        tag_steps.extend(_bump_and_land(bump_step, branch, body) + [tag_step, gr_step])

    # The release notes are a page of the docs site, so they live under `docs/`
    # with every other page. A copy at the repository root would be a second
    # source of truth for the same text and would drift on the first edit.
    notes_step = {"kind": "notes", "component": "notes", "path": f"docs/releases/{version}.md"}
    tag_self_step = {"kind": "tag_self", "component": "coxswain", "tag": new_tag}
    umbrella_gr_step = {"kind": "github_release", "component": "coxswain", "repo": umbrella_slug, "tag": new_tag,
                         "title": f"coxswain {version}", "notes_path": f"docs/releases/{version}.md", "heading": None}

    if current_parsed is not None:
        if _sort_key(parsed) < _sort_key(current_parsed):
            return _refuse(version, f"{version} is not greater than the current version {current}")
        if _sort_key(parsed) == _sort_key(current_parsed):
            steps = tag_steps + [notes_step, tag_self_step, umbrella_gr_step]
            return _with_wait_workflows(steps) + _tap_formula_steps(steps, version, tools_repository_url,
                                                                    umbrella_slug, tap_state)

    branch = f"release/{version}"
    subject = f"manifest: bump to {version} to match the tag"
    bump_step = {"kind": "bump_manifest", "component": "manifest", "from": current, "to": version,
                 "branch": branch, "commit_subject": subject}
    body = f"Bumps manifest.toml version to {version} to match tag {new_tag}."
    steps = tag_steps + [notes_step] + _bump_and_land(bump_step, branch, body) + [tag_self_step, umbrella_gr_step]
    return _with_wait_workflows(steps) + _tap_formula_steps(steps, version, tools_repository_url,
                                                            umbrella_slug, tap_state)


def release_index_text(existing_index: str, version: str, manifest: Mapping) -> str:
    """`existing_index` with its `## VERSION` section for `version` replaced
    by `index_section`'s current rendering, or that rendering appended when
    no such section exists yet. Keyed on the heading, not the rendered text,
    so a rerun after `manifest` has changed updates that section in place
    instead of leaving a stale one beside a fresh one. Sections split on any
    `## ` at the start of a line, blank line before it or not, since a
    hand-written `index.md` cannot be relied on for that blank line and a
    missed one must never delete a neighbouring version's entry."""
    component_tags = {name: f"v{version}" if spec.get("lockstep", True) else str(spec.get("tag"))
                       for name, spec in manifest.get("components", {}).items()}
    section = index_section(version, component_tags, manifest.get("components", {}))
    headings = (f"## {version}", f"## `{version}`")
    body = existing_index.strip("\n")
    sections = [s.strip("\n") for s in re.split(r"\n(?=## )", body)] if body else []
    at = next((i for i, s in enumerate(sections)
               if any(s == h or s.startswith(f"{h}\n") for h in headings)), None)
    new_sections = [*sections, section] if at is None else [*sections[:at], section, *sections[at + 1:]]
    return "\n\n".join(new_sections) + "\n"


def versions_oldest_first(versions: Iterable[str]) -> list[str]:
    """`versions` (bare, no `v` prefix) ordered oldest-first by the same
    semver-with-beta rules `release_plan` sorts by; a name that doesn't parse
    as a version sorts after every one that does, stable among themselves."""
    parsed = [(v, _parse_semver(v)) for v in versions]
    return [v for v, p in sorted(parsed, key=lambda vp: (1, ()) if vp[1] is None else (0, _sort_key(vp[1])))]


def component_dir(root: str, name: str, overrides: Mapping[str, str] | None = None) -> str:
    """The directory `cox install` would have cloned `name` into under
    `root`, unless `overrides` names a different path for that component —
    a developer machine's `--checkout name=path`."""
    if overrides and name in overrides:
        return overrides[name]
    plain = Path(root) / name
    try:
        if not plain.is_dir():
            # A developer checkout is cloned under its repository name
            # (`coxswain-graphs`), not its manifest key (`graphs`); without an
            # override, prefer the directory that exists over one that does not.
            prefixed = Path(root) / f"coxswain-{name}"
            if prefixed.is_dir():
                return str(prefixed)
    except OSError:
        # An unreadable root (CI runs the pure-shape tests against "/root")
        # is not a reason to change the answer this function always gave.
        pass
    return str(plain)


def component_version(pyproject_toml_text: str) -> str | None:
    """The version a checkout's own pyproject.toml declares: `[project]
    version`, or `[tool.poetry] version` when the former is absent; `None`
    when neither table carries one."""
    parsed = tomllib.loads(pyproject_toml_text)
    found = parsed.get("project", {}).get("version")
    return found if found is not None else parsed.get("tool", {}).get("poetry", {}).get("version")


def tag_argv(directory: str, version: str) -> list[str]:
    """`git -C <directory> tag -a v<version> -m "coxswain <version>"`."""
    return ["git", "-C", directory, "tag", "-a", "v" + version, "-m", f"coxswain {version}"]


def push_argv(directory: str, version: str) -> list[str]:
    """`git -C <directory> push origin v<version>`."""
    return ["git", "-C", directory, "push", "origin", "v" + version]


def bumped_version_line(text: str, to: str) -> str:
    """`text` (a pyproject.toml or manifest.toml) with its first top-level
    `version = "..."` line rewritten to `to`; unchanged if none is found."""
    return re.sub(r'(?m)^version = "[^"]*"', f'version = "{to}"', text, count=1)


def bumped_uv_lock_text(text: str, package: str, to: str) -> str:
    """`text` (uv.lock) with `package`'s own `[[package]]` stanza's `version`
    line rewritten to `to`; every other package's stanza, and the lockfile's
    own top-of-file `version = 1` schema line, are left untouched."""
    blocks = re.split(r"(?=\n\[\[package\]\])", text)
    return "".join(
        re.sub(r'(?m)^version = "[^"]*"', f'version = "{to}"', b, count=1)
        if re.search(rf'(?m)^name = "{re.escape(package)}"$', b) else b
        for b in blocks)


def checkout_branch_argv(directory: str, branch: str) -> list[str]:
    """`git -C <directory> checkout -b <branch>` — cut from whatever commit
    `directory` is on, which `_checkout_ready` has already required to be
    the clean default branch."""
    return ["git", "-C", directory, "checkout", "-b", branch]


def add_argv(directory: str, *paths: str) -> list[str]:
    """`git -C <directory> add -- <paths...>`."""
    return ["git", "-C", directory, "add", "--", *paths]


def commit_argv(directory: str, subject: str, *paths: str) -> list[str]:
    """`git -C <directory> commit -m "<subject>" -- <paths...>`."""
    return ["git", "-C", directory, "commit", "-m", subject, "--", *paths]


def push_branch_argv(directory: str, branch: str) -> list[str]:
    """`git -C <directory> push -u origin <branch>` — a branch push, unlike
    `push_argv`'s tag push."""
    return ["git", "-C", directory, "push", "-u", "origin", branch]


def pr_create_argv(title: str, body: str) -> list[str]:
    """`gh pr create --title "<title>" --body "<body>"`, run with `cwd` set
    to the checkout the branch was pushed from."""
    return ["gh", "pr", "create", "--title", title, "--body", body]


def pr_checks_argv() -> list[str]:
    """`gh pr checks --watch --fail-fast`, run against the checkout's current
    branch's own pull request."""
    return ["gh", "pr", "checks", "--watch", "--fail-fast"]


def pr_merge_argv() -> list[str]:
    """`gh pr merge --squash --delete-branch`."""
    return ["gh", "pr", "merge", "--squash", "--delete-branch"]


def checkout_ref_argv(directory: str, ref: str) -> list[str]:
    """`git -C <directory> checkout <ref>` — switches back to an existing
    branch, unlike `checkout_branch_argv`'s `-b`; run after a merge lands the
    bump upstream, so the very next `tag`/`tag_self` step tags what actually
    merged instead of the pre-squash commit the bump branch is still on."""
    return ["git", "-C", directory, "checkout", ref]


def pull_argv(directory: str, branch: str) -> list[str]:
    """`git -C <directory> pull origin <branch>` — brings the squash-merge
    commit `checkout_ref_argv` just switched onto down from `origin`."""
    return ["git", "-C", directory, "pull", "origin", branch]


def github_release_view_argv(tag: str) -> list[str]:
    """`gh release view <tag>` — the idempotence check `github_release` runs first."""
    return ["gh", "release", "view", tag]


def github_release_body_argv(tag: str) -> list[str]:
    """`gh release view <tag> --json body -q .body` — just the body text, with
    no header lines above a `--` separator to strip before comparing it."""
    return ["gh", "release", "view", tag, "--json", "body", "-q", ".body"]


def github_release_create_argv(tag: str, title: str, notes_path: str) -> list[str]:
    """`gh release create <tag> --verify-tag --title "<title>" --notes-file <notes_path>`."""
    return ["gh", "release", "create", tag, "--verify-tag", "--title", title, "--notes-file", notes_path]


def github_release_edit_argv(tag: str, notes_path: str) -> list[str]:
    """`gh release edit <tag> --notes-file <notes_path>` — run instead of `create`
    once `github_release_view_argv` shows the release already exists."""
    return ["gh", "release", "edit", tag, "--notes-file", notes_path]


def extract_release_notes(notes_path: str, heading: str) -> str | None:
    """The text of `notes_path` from the line equal to `heading` (e.g.
    `"## coxswain-crew"`) through the line before the next `"## "` heading or
    end of file, or `None` when the file is missing or `heading` is absent.
    Reads the file itself, unlike the rest of this module: a later backfill
    task calls this the same way against a past release's notes file, with
    nothing else here to import alongside it."""
    try:
        lines = Path(notes_path).read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return None
    start = next((i for i, line in enumerate(lines) if line.rstrip("\n") == heading), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "".join(lines[start:end])


def umbrella_release_slug(manifest: Mapping, tools_repository_url: str | None = None) -> str | None:
    """The umbrella's `org/repo`: `manifest["coxswain"]["repo"]` when the
    manifest names one, else `tools_repository_url` (the tools package's own
    `Repository` URL, gathered by the edge — `cli.py` reads it from installed
    package metadata, the way `existing_tags` is gathered) with a trailing
    `-tools` stripped from the repo name — `coxswain-tools` ships one name
    away from the umbrella it releases. `None` when neither source is given."""
    repo = manifest.get("coxswain", {}).get("repo")
    if repo:
        return repo
    if not tools_repository_url:
        return None
    org, name = tools_repository_url.rstrip("/").split("/")[-2:]
    return f"{org}/{name[:-len('-tools')] if name.endswith('-tools') else name}"


_MANIFEST_SECTION_RE = re.compile(r"^\[components\.([\w-]+)\]\s*$")
_MANIFEST_LOCKSTEP_FALSE_RE = re.compile(r"^\s*lockstep\s*=\s*false\s*$", re.IGNORECASE)
_MANIFEST_VERSION_RE = re.compile(r'(\s*version\s*=\s*")[^"]*(")')
_MANIFEST_TAG_RE = re.compile(r'(\s*tag\s*=\s*")v[^"]*(")')
# Any `[table]` or `[table.sub]` header, to know which table an inline-table
# line sits under — an inline component only ever appears directly under the
# bare `[components]` table, never under `[coxswain]` or some other table
# that happens to hold an unrelated inline table with its own `tag` key.
_MANIFEST_TABLE_RE = re.compile(r"^\[([\w.-]+)\]\s*$")
# An inline-table component opens on a `name = {` line and may close on a later one.
_MANIFEST_INLINE_START_RE = re.compile(r"^\s*([\w-]+)\s*=\s*\{(.*)$")
_MANIFEST_INLINE_LOCKSTEP_FALSE_RE = re.compile(r"\blockstep\s*=\s*false\b", re.IGNORECASE)
_MANIFEST_INLINE_TAG_RE = re.compile(r'(\btag\s*=\s*")v[^"]*(")')


def _inline_owners(lines: list[str]) -> list[str | None]:
    """The component each line belongs to: the `name = {` entry directly under
    `[components]` whose braces are still open on that line, else None.
    Braces are counted per line; a brace inside a quoted string is not handled."""
    owners = []
    table = None
    owner = None
    depth = 0
    for line in lines:
        text = line.rstrip("\n")
        header = _MANIFEST_TABLE_RE.match(text)
        start = _MANIFEST_INLINE_START_RE.match(text)
        if depth > 0:
            depth = max(0, depth + text.count("{") - text.count("}"))
        else:
            table = header.group(1) if header else table
            opens = table == "components" and start is not None
            owner = start.group(1) if opens else None
            depth = max(0, text.count("{") - text.count("}")) if opens else 0
        owners.append(owner)
    return owners


def _pinned_components(text: str) -> set[str]:
    """Component names whose `[components.<name>]` section, or whose own
    `name = { ... }` inline table directly under `[components]`, on one line
    or several, declares `lockstep = false` anywhere in it."""
    section = None
    pinned = set()
    lines = text.splitlines()
    for line, owner in zip(lines, _inline_owners(lines)):
        m = _MANIFEST_SECTION_RE.match(line)
        t = _MANIFEST_TABLE_RE.match(line)
        if m:
            section = m.group(1)
        elif t:
            section = None
        elif section and _MANIFEST_LOCKSTEP_FALSE_RE.match(line):
            pinned.add(section)
        elif owner and _MANIFEST_INLINE_LOCKSTEP_FALSE_RE.search(line):
            pinned.add(owner)
    return pinned


def rejoined(steps: list[dict]) -> set[str]:
    """Component names `release_plan` gave a `rejoin` step, for `bumped_manifest_text`'s `rejoining`."""
    return {s["component"] for s in steps if s["kind"] == "rejoin"}


def bumped_manifest_text(text: str, version: str, rejoining: Iterable[str] = ()) -> str:
    """`text` with every `version = "..."` value, and every `tag = "v..."`
    value outside a `lockstep = false` component's section — whether the
    component is a `[components.<name>]` section or a `name = { ... }`
    inline table directly under `[components]`, on one line or several —
    rewritten to `version`; comments, blank lines and layout untouched, and
    every other key on an inline table's lines kept byte-identical. A pinned
    component's own `tag` is left exactly as it reads, unless named in
    `rejoining`."""
    new_tag = "v" + version
    pinned = _pinned_components(text) - set(rejoining)
    section = None
    out = []
    lines = text.splitlines(keepends=True)
    for line, owner in zip(lines, _inline_owners(lines)):
        stripped = line.rstrip("\n")
        m = _MANIFEST_SECTION_RE.match(stripped)
        t = _MANIFEST_TABLE_RE.match(stripped)
        if m:
            section = m.group(1)
        elif t:
            section = None
        if _MANIFEST_VERSION_RE.match(line):
            out.append(_MANIFEST_VERSION_RE.sub(lambda m: f"{m.group(1)}{version}{m.group(2)}", line))
        elif owner:
            keep = owner in pinned
            out.append(line if keep else _MANIFEST_INLINE_TAG_RE.sub(lambda m: f"{m.group(1)}{new_tag}{m.group(2)}", line))
        elif _MANIFEST_TAG_RE.match(line) and section not in pinned:
            out.append(_MANIFEST_TAG_RE.sub(lambda m: f"{m.group(1)}{new_tag}{m.group(2)}", line))
        else:
            out.append(line)
    return "".join(out)


_FORMULA_URL_RE = re.compile(r'^(\s*url\s+")[^"]*(")')
_FORMULA_SHA_RE = re.compile(r'^(\s*sha256\s+")[^"]*(")')
_FORMULA_RESOURCE_RE = re.compile(r"^\s*resource\s")


def bumped_formula_text(text: str, version: str, sdist_url: str, sha256: str) -> str:
    """`text` with the `url` and `sha256` lines before the first `resource` stanza rewritten; `version` is carried by `sdist_url`."""
    out = []
    in_resource = False
    for line in text.splitlines(keepends=True):
        in_resource = in_resource or _FORMULA_RESOURCE_RE.match(line) is not None
        if not in_resource and _FORMULA_URL_RE.match(line):
            out.append(_FORMULA_URL_RE.sub(lambda m: f"{m.group(1)}{sdist_url}{m.group(2)}", line))
        elif not in_resource and _FORMULA_SHA_RE.match(line):
            out.append(_FORMULA_SHA_RE.sub(lambda m: f"{m.group(1)}{sha256}{m.group(2)}", line))
        else:
            out.append(line)
    return "".join(out)
