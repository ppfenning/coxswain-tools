"""Which work-state backend the provider profile selects: `store` or `files`."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_tools.store_url import read_provider_profile


def work_state_mode(profile: Mapping[str, Any]) -> str:
    """Only the exact string `store` selects store mode; a missing key, null or any other value is `files`."""
    return "store" if profile.get("work_state") == "store" else "files"


def work_state_mode_at(provider_profile: Path | str) -> str:
    """Edge. The mode for the provider profile at `provider_profile`; an unreadable profile is `files`."""
    return work_state_mode(read_provider_profile(provider_profile))
