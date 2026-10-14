"""Pure expand, contract and host-map of a recorded repo path. The home directory is always an argument."""

from __future__ import annotations

__all__ = ["HOME_ROOTS", "contract", "expand", "host_form"]

# Linux home root, then macOS home root. A path under either, plus one user name, is some machine's home.
HOME_ROOTS = ("/home", "/Users")


def _rest_under_home(path: str, home: str) -> str | None:
    """The part of an absolute path below `home` or below another machine's home; "" for a home itself."""
    if not path.startswith("/"):
        return None
    p, h = path.rstrip("/"), home.rstrip("/")
    if p == h:
        return ""
    if p.startswith(h + "/"):
        return p[len(h) + 1 :]
    for root in HOME_ROOTS:
        if p.startswith(root + "/"):
            user, _, rest = p[len(root) + 1 :].partition("/")
            if user:
                return rest
    return None


def expand(recorded: str, home: str) -> str:
    """The absolute path on the machine whose home is `home`; anything not home-relative is unchanged."""
    h = home.rstrip("/")
    if recorded == "~" or recorded.startswith("~/"):
        rest = recorded[2:].rstrip("/")
    else:
        found = _rest_under_home(recorded, home)
        if found is None:
            return recorded
        rest = found
    return f"{h}/{rest}" if rest else h


def contract(path: str, home: str) -> str:
    """The tilde form of a path under `home` or another machine's home; idempotent, trailing slashes dropped."""
    if path == "~" or path.startswith("~/"):
        rest: str | None = path[2:].rstrip("/")
    elif path.startswith("~"):
        return path
    else:
        rest = _rest_under_home(path, home)
    if rest is None:
        return path
    return f"~/{rest}" if rest else "~"


def host_form(path: str, home: str) -> str:
    """The home-relative form an ssh command needs, since a quoted tilde is never expanded there."""
    contracted = contract(path, home)
    if contracted == "~":
        return "."
    return contracted[2:] if contracted.startswith("~/") else contracted
