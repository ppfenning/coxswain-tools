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


def _drift_line(d) -> str:
    a = f"{d.a_file}:{d.a_line}" if d.a_line is not None else d.a_file
    b = f"{d.b_file}:{d.b_line}" if d.b_line is not None else d.b_file
    return f"{d.check}: {a} <-> {b} — {d.correction}"


def gate(drifts: Sequence, allow_reason: str | None) -> list[dict]:
    """Refuse steps naming each drift, or one note step when `allow_reason`
    says why they stand; empty when `drifts` is empty. A `versions` drift is
    never folded into the allowed-reason note — it always refuses."""
    if not drifts:
        return []
    blocking = [d for d in drifts if d.check == "versions"]
    allowable = [d for d in drifts if d.check != "versions"]
    blocking_steps = [{"kind": "refuse", "component": d.check, "detail": _drift_line(d)} for d in blocking]
    if not allowable:
        return blocking_steps
    if allow_reason is None:
        return blocking_steps + [{"kind": "refuse", "component": d.check, "detail": _drift_line(d)} for d in allowable]
    plural = "" if len(allowable) == 1 else "s"
    return blocking_steps + [{"kind": "note", "component": "release-check",
             "detail": f"{allow_reason} ({len(allowable)} drift{plural} allowed)"}]


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


def release_plan(manifest: Mapping, version: str, existing_tags: Mapping[str, list[str] | None],
                  component_versions: Mapping[str, str | None] | None = None,
                  pinned_commits: Mapping[str, int] | None = None) -> list[dict]:
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
    proceeds with no `bump_manifest` step — the manifest already says so."""
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
    tag_steps = []
    for name, spec in repo_components:
        if not spec.get("lockstep", True):
            commits = (pinned_commits or {}).get(name) or 0
            tag_steps.append({"kind": "rejoin", "component": name, "repo": spec["repo"], "tag": new_tag,
                               "from": spec["tag"], "commits": commits} if commits else
                              {"kind": "pinned", "component": name, "tag": spec["tag"]})
            continue
        tag_step = {"kind": "tag", "component": name, "repo": spec["repo"], "tag": new_tag}
        found = component_versions.get(name)
        found_parsed = _parse_semver(found) if found is not None else None
        if found_parsed is None or _sort_key(found_parsed) >= _sort_key(parsed):
            tag_steps.append(tag_step)
            continue
        branch = f"release/{version}"
        subject = f"pyproject: bump to {version} to match the tag"
        bump_step = {"kind": "bump_pyproject", "component": name, "repo": spec["repo"],
                     "branch": branch, "commit_subject": subject, "from": found, "to": version}
        body = f"Bumps {name}'s pyproject.toml version to {version} to match tag {new_tag}."
        tag_steps.extend(_bump_and_land(bump_step, branch, body) + [tag_step])

    # The release notes are a page of the docs site, so they live under `docs/`
    # with every other page. A copy at the repository root would be a second
    # source of truth for the same text and would drift on the first edit.
    notes_step = {"kind": "notes", "component": "notes", "path": f"docs/releases/{version}.md"}
    tag_self_step = {"kind": "tag_self", "component": "coxswain", "tag": new_tag}

    if current_parsed is not None:
        if _sort_key(parsed) < _sort_key(current_parsed):
            return _refuse(version, f"{version} is not greater than the current version {current}")
        if _sort_key(parsed) == _sort_key(current_parsed):
            return _with_wait_workflows(tag_steps + [notes_step, tag_self_step])

    branch = f"release/{version}"
    subject = f"manifest: bump to {version} to match the tag"
    bump_step = {"kind": "bump_manifest", "component": "manifest", "from": current, "to": version,
                 "branch": branch, "commit_subject": subject}
    body = f"Bumps manifest.toml version to {version} to match tag {new_tag}."
    return _with_wait_workflows(tag_steps + [notes_step] + _bump_and_land(bump_step, branch, body) + [tag_self_step])


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


_MANIFEST_SECTION_RE = re.compile(r"^\[components\.([\w-]+)\]\s*$")
_MANIFEST_LOCKSTEP_FALSE_RE = re.compile(r"^\s*lockstep\s*=\s*false\s*$", re.IGNORECASE)
_MANIFEST_VERSION_RE = re.compile(r'(\s*version\s*=\s*")[^"]*(")')
_MANIFEST_TAG_RE = re.compile(r'(\s*tag\s*=\s*")v[^"]*(")')


def _pinned_components(text: str) -> set[str]:
    """Component names whose `[components.<name>]` section declares
    `lockstep = false` anywhere in it."""
    section = None
    pinned = set()
    for line in text.splitlines():
        m = _MANIFEST_SECTION_RE.match(line)
        if m:
            section = m.group(1)
        elif section and _MANIFEST_LOCKSTEP_FALSE_RE.match(line):
            pinned.add(section)
    return pinned


def rejoined(steps: list[dict]) -> set[str]:
    """Component names `release_plan` gave a `rejoin` step, for `bumped_manifest_text`'s `rejoining`."""
    return {s["component"] for s in steps if s["kind"] == "rejoin"}


def bumped_manifest_text(text: str, version: str, rejoining: Iterable[str] = ()) -> str:
    """`text` with every `version = "..."` value, and every `tag = "v..."`
    value outside a `lockstep = false` component's section, rewritten to
    `version` — comments, blank lines and layout untouched; a pinned
    component's own `tag` line is left exactly as it reads, unless named in `rejoining`."""
    new_tag = "v" + version
    pinned = _pinned_components(text) - set(rejoining)
    section = None
    out = []
    for line in text.splitlines(keepends=True):
        m = _MANIFEST_SECTION_RE.match(line.rstrip("\n"))
        if m:
            section = m.group(1)
        if _MANIFEST_VERSION_RE.match(line):
            out.append(_MANIFEST_VERSION_RE.sub(lambda m: f"{m.group(1)}{version}{m.group(2)}", line))
        elif _MANIFEST_TAG_RE.match(line) and section not in pinned:
            out.append(_MANIFEST_TAG_RE.sub(lambda m: f"{m.group(1)}{new_tag}{m.group(2)}", line))
        else:
            out.append(line)
    return "".join(out)
