"""Pure wording for the versions report: why every component can look drifted, and when a pin cannot be met."""

import re

_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)")


def version_key(tag: str) -> tuple[int, ...] | None:
    """Numeric release of a tag. 'v0.26.0', '0.26.0' and 'v0.26.0-3-gabc' all give (0, 26); None if no leading number."""
    found = _VERSION.match(tag.strip())
    if found is None:
        return None
    parts = [int(n) for n in found.group(1).split(".")]
    while parts and parts[-1] == 0:
        parts = parts[:-1]
    return tuple(parts)


def same_release(a: str, b: str) -> bool:
    """True when both tags name one release regardless of 'v' prefix or git-describe suffix; else plain string equality."""
    ka, kb = version_key(a), version_key(b)
    return a == b if ka is None or kb is None else ka == kb


def umbrella_hint(umbrella_tag: str | None, release_tag: str | None) -> str | None:
    """One line when the umbrella checkout is provably older than the release; None when a tag is missing, equal, newer or unparseable."""
    if umbrella_tag is None or release_tag is None:
        return None
    ku, kr = version_key(umbrella_tag), version_key(release_tag)
    if ku is None or kr is None or ku >= kr:
        return None
    return (
        f"note: umbrella checkout is at {umbrella_tag} but the installed release is {release_tag}; "
        f"pins below come from the umbrella checkout at the older tag"
    )


def status_label(status: str, pinned: str, release_tag: str, offered_tags: frozenset[str]) -> str:
    """'untagged' for a drift whose release the component never tagged; every other status is unchanged."""
    if status != "drift" or same_release(pinned, release_tag):
        return status
    return status if any(same_release(t, release_tag) for t in offered_tags) else "untagged"
