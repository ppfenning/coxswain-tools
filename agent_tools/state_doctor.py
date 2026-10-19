"""Pure: the doctor check that fails when runtime files sit under the workspace. No I/O."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath

from agent_tools.state_paths import classify

MAX_EXAMPLES = 5


@dataclass(frozen=True)
class StateCheck:
    ok: bool
    message: str


def check_runtime_in_workspace(names: Sequence[str], workspace: PurePath, state_root: PurePath) -> StateCheck:
    """Fail when any listed workspace name is runtime state; records never fail. Names keep listing order."""
    runtime = tuple(n for n in names if classify(n, workspace, state_root).kind == "runtime")
    if not runtime:
        return StateCheck(True, "no runtime files under the workspace")
    shown = ", ".join(runtime[:MAX_EXAMPLES])
    more = len(runtime) - MAX_EXAMPLES
    tail = f" and {more} more" if more > 0 else ""
    return StateCheck(
        False,
        f"{len(runtime)} runtime files under the workspace: {shown}{tail}; run the setup migrate step to move them",
    )
