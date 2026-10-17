"""Extras of an installed uv tool, read from its receipt, for the setup install plan."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path


def parse_receipt_extras(text: str) -> tuple[str, ...]:
    """The strings at `tool.requirements[0].extras`, in file order; () for anything else."""
    try:
        first = tomllib.loads(text)["tool"]["requirements"][0]
        extras = first["extras"]
    except (ValueError, KeyError, IndexError, TypeError):
        return ()
    return tuple(x for x in extras if isinstance(x, str)) if isinstance(extras, list) else ()


def default_tool_dir(env: Mapping[str, str], home: str) -> str:
    """Where uv keeps tools: `UV_TOOL_DIR`, else `$XDG_DATA_HOME/uv/tools`, else `<home>/.local/share/uv/tools`."""
    if env.get("UV_TOOL_DIR"):
        return env["UV_TOOL_DIR"]
    data_home = env.get("XDG_DATA_HOME") or f"{home}/.local/share"
    return f"{data_home}/uv/tools"


def read_tool_extras(tool_dir: str, package: str) -> tuple[str, ...]:
    """Edge. Extras in `<tool_dir>/<package>/uv-receipt.toml`; () when the file is missing or unreadable."""
    try:
        text = (Path(tool_dir) / package / "uv-receipt.toml").read_text()
    except OSError:
        return ()
    return parse_receipt_extras(text)
