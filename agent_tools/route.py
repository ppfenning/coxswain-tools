"""Pure core for the routing layer: profile parsing and naming. No file
reads, no env access — every function here takes plain arguments and
returns plain values."""

from __future__ import annotations

import re

__all__ = ["ProfileError", "parse_profile", "slugify", "next_run_id"]

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
