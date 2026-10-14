"""One canonical release version form, and a parser for every spelling of it.

Canonical is bare MAJOR.MINOR.PATCH, with an optional -PRERELEASE. The git tag is "v" plus that form.
"""

import re
from dataclasses import dataclass
from functools import total_ordering

_SPELLING = re.compile(r"(v)?(\d+)(?:\.(\d+)(?:\.(\d+))?)?(?:-([0-9A-Za-z.]+))?")


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: str | None = None

    def __lt__(self, other: Version) -> bool:
        return sort_key(self) < sort_key(other)


def sort_key(v: Version) -> tuple[int, int, int, int, str]:
    """A prerelease sorts below its release; two prereleases compare as plain strings."""
    return (v.major, v.minor, v.patch, 0 if v.prerelease else 1, v.prerelease or "")


def parse(text: str) -> Version | None:
    """Read bare, v-prefixed, and short forms. A lone number means 0.N.0 and only after v."""
    m = _SPELLING.fullmatch(text.strip())
    if m is None:
        return None
    v, first, second, third, pre = m.groups()
    if second is None:
        return Version(0, int(first), 0, pre) if v else None
    return Version(int(first), int(second), int(third or 0), pre)


def canonical(v: Version) -> str:
    base = f"{v.major}.{v.minor}.{v.patch}"
    return f"{base}-{v.prerelease}" if v.prerelease else base


def tag(v: Version) -> str:
    return f"v{canonical(v)}"
