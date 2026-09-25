"""Pure core for .agent-generate: parse the file and plan commands. No I/O, clock or environment reads."""

from __future__ import annotations

__all__ = ["build_env", "parse_generate_file", "plan_commands"]


def parse_generate_file(text: str) -> tuple[str, ...]:
    """One command per line; blank lines and lines starting with `#` are dropped, inline `#` is kept."""
    stripped = (line.strip() for line in text.splitlines())
    return tuple(line for line in stripped if line and not line.startswith("#"))


def build_env(worktree: str, umbrella: str | None) -> dict[str, str]:
    """Variables to merge over the process environment; COX_UMBRELLA only when umbrella is set."""
    return {"COX_WORKTREE": worktree, **({} if umbrella is None else {"COX_UMBRELLA": umbrella})}


def plan_commands(commands: tuple[str, ...], umbrella: str | None) -> tuple[tuple[str, str | None], ...]:
    """Pair each command with None to run it, or a one-line note to skip it."""
    return tuple(
        (command, f"skipped {command!r}: umbrella_dir is unset")
        if umbrella is None and "COX_UMBRELLA" in command
        else (command, None)
        for command in commands
    )
