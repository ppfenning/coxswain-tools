"""The version cox reports: the checkout's `git describe --tags` on an editable install, else the installed metadata.

`choose_version` is pure. `package_version` is the edge: it reads the installed metadata and, only when the
package sits directly in a git checkout, asks git through an injected `run`.
"""

import importlib.metadata
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

DISTRIBUTION = "coxswain-tools"
UNKNOWN = "unknown"
GIT_TIMEOUT_S = 5


def choose_version(described: str | None, installed: str) -> str:
    """Pure. `described` stripped when it is a non-empty string, otherwise `installed`."""
    stripped = described.strip() if isinstance(described, str) else ""
    return stripped or installed


def _installed() -> str:
    try:
        return importlib.metadata.version(DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return UNKNOWN


def _described(checkout: Path, run: Callable[..., Any]) -> str | None:
    """Edge. `git describe --tags` of `checkout`; None on a non-zero exit, a missing git or a timeout."""
    try:
        done = run(["git", "-C", str(checkout), "describe", "--tags"], capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def package_version(package_dir: Path | None = None, run: Callable[..., Any] = subprocess.run) -> str:
    """Edge. The installed version, overridden by the checkout's `git describe --tags` when `package_dir`'s parent
    holds a `.git` entry. The check on `.git` keeps a wheel installed inside an unrelated repository from
    reporting that repository's tag. `package_dir` defaults to the `agent_tools` package directory."""
    checkout = (package_dir or Path(__file__).resolve().parent).parent
    described = _described(checkout, run) if (checkout / ".git").exists() else None
    return choose_version(described, _installed())
