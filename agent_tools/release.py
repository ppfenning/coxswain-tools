"""The pure planner over coxswain's lockstep release: what `cox release
<version> --dry-run` prints, and the argv `cox release <version>` runs
through an injected runner. No filesystem, subprocess or clock here —
`cli.py` gathers the existing tags, resolves checkout directories, and
runs the git commands at the edge."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

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


def is_maintainer_remote(url: str) -> bool:
    """True when `url` names the ppfenning/coxswain umbrella — the one
    checkout a lockstep release is allowed to run against."""
    return "ppfenning/coxswain" in url


def parse_ls_remote(text: str) -> list[str]:
    """Tag names from `git ls-remote --tags` output, peeled `^{}` refs skipped.
    Pure: the edge fetches the text, this decides what it means."""
    refs = (line.split("\t", 1)[1] for line in text.splitlines() if "\t" in line)
    return [ref[len("refs/tags/"):] for ref in refs if ref.startswith("refs/tags/") and not ref.endswith("^{}")]


def release_plan(manifest: Mapping, version: str, existing_tags: Mapping[str, list[str] | None]) -> list[dict]:
    """Steps in order: `tag` for each `repo` component, one `bump_manifest`,
    one `notes`, one `tag_self`. A single `refuse` step, naming the reason,
    when `version` is not valid semver-with-optional-beta, when the tag
    already exists on any component (or on the umbrella, when `existing_tags`
    carries a `"coxswain"` key), or when `version` is strictly less than the
    manifest's current version by semver-with-beta rules.

    `version` equal to the current version is the first cut of the version
    the manifest already declares: nothing is tagged yet, so the plan
    proceeds with no `bump_manifest` step — the manifest already says so."""
    parsed = _parse_semver(version)
    if parsed is None:
        return _refuse(version, f"{version!r} is not a valid version (expected X.Y.Z or X.Y.Z-beta.N)")

    new_tag = "v" + version
    components = manifest.get("components", {})
    repo_components = [(name, spec) for name, spec in components.items() if spec.get("repo")]

    # Three states per component: a list of tags, an empty list (reachable, no
    # tags), or None (the remote could not be read). Unknown is not clean: a
    # reused tag is the one thing a release must never risk, so None refuses.
    unknown = sorted(name for name, _ in repo_components if existing_tags.get(name) is None)
    if unknown:
        return _refuse(", ".join(unknown), f"tags unknown for {', '.join(unknown)} (remote unreadable); refusing rather than risk reusing {new_tag}")

    collision_sources = [name for name, _ in repo_components] + (["coxswain"] if "coxswain" in existing_tags else [])
    colliding = sorted(name for name in collision_sources if new_tag in (existing_tags.get(name) or []))
    if colliding:
        return _refuse(", ".join(colliding), f"tag {new_tag} already exists on {', '.join(colliding)}")

    current = manifest.get("coxswain", {}).get("version")
    current_parsed = _parse_semver(current) if current is not None else None
    tag_steps = [{"kind": "tag", "component": name, "repo": spec["repo"], "tag": new_tag}
                 for name, spec in repo_components]
    # The release notes are a page of the docs site, so they live under `docs/`
    # with every other page. A copy at the repository root would be a second
    # source of truth for the same text and would drift on the first edit.
    notes_step = {"kind": "notes", "component": "notes", "path": f"docs/releases/{version}.md"}
    tag_self_step = {"kind": "tag_self", "component": "coxswain", "tag": new_tag}

    if current_parsed is not None:
        if _sort_key(parsed) < _sort_key(current_parsed):
            return _refuse(version, f"{version} is not greater than the current version {current}")
        if _sort_key(parsed) == _sort_key(current_parsed):
            return tag_steps + [notes_step, tag_self_step]

    bump_step = {"kind": "bump_manifest", "component": "manifest", "from": current, "to": version}
    return tag_steps + [bump_step, notes_step, tag_self_step]


def component_dir(root: str, name: str, overrides: Mapping[str, str] | None = None) -> str:
    """The directory `cox install` would have cloned `name` into under
    `root`, unless `overrides` names a different path for that component —
    a developer machine's `--checkout name=path`."""
    if overrides and name in overrides:
        return overrides[name]
    return str(Path(root) / name)


def tag_argv(directory: str, version: str) -> list[str]:
    """`git -C <directory> tag -a v<version> -m "coxswain <version>"`."""
    return ["git", "-C", directory, "tag", "-a", "v" + version, "-m", f"coxswain {version}"]


def push_argv(directory: str, version: str) -> list[str]:
    """`git -C <directory> push origin v<version>`."""
    return ["git", "-C", directory, "push", "origin", "v" + version]


_MANIFEST_VERSION_RE = re.compile(r'(?m)^(\s*version\s*=\s*")[^"]*(")')
_MANIFEST_TAG_RE = re.compile(r'(?m)^(\s*tag\s*=\s*")v[^"]*(")')


def bumped_manifest_text(text: str, version: str) -> str:
    """`text` with every `version = "..."` and `tag = "v..."` value
    rewritten to `version` — comments, blank lines and layout untouched."""
    new_tag = "v" + version
    with_version = _MANIFEST_VERSION_RE.sub(lambda m: f"{m.group(1)}{version}{m.group(2)}", text)
    return _MANIFEST_TAG_RE.sub(lambda m: f"{m.group(1)}{new_tag}{m.group(2)}", with_version)
