"""Checkout-aware version: prefer `git describe` in a real checkout over package metadata."""

from __future__ import annotations

import importlib.metadata
import subprocess
from pathlib import Path


def choose_version(described: str | None, installed: str) -> str:
    """Return the stripped `described` when non-empty, else `installed`."""
    stripped = (described or "").strip()
    return stripped if stripped else installed


def _installed_version() -> str:
    try:
        return importlib.metadata.version("coxswain-tools")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _describe(root: Path, run) -> str | None:
    try:
        result = run(
            ["git", "-C", str(root), "describe", "--tags"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return None
    return result.stdout if result.returncode == 0 else None


def package_version(package_dir: Path | None = None, run=subprocess.run) -> str:
    """Version of this checkout, or the installed metadata version outside one.

    Only a `.git` entry directly above the package counts as a checkout, so a wheel
    installed inside an unrelated repository never reports that repository's tag.
    """
    root = (package_dir or Path(__file__).resolve().parent).parent
    described = _describe(root, run) if (root / ".git").exists() else None
    return choose_version(described, _installed_version())
