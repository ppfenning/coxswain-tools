"""Talk to the voice HUD over its HTTP contracts. Every call is one request; nothing is inferred."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["DEFAULT_BASE", "cast", "clear_inbox", "get", "inbox", "post", "post_ops", "say", "wait_for_inbox"]

DEFAULT_BASE = "http://127.0.0.1:8123"


def get(route: str, *, base: str = DEFAULT_BASE, timeout: float = 3.0) -> Any:
    with urllib.request.urlopen(f"{base}{route}", timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def post(route: str, body: Mapping[str, Any], *, base: str = DEFAULT_BASE, timeout: float = 5.0) -> Any:
    req = urllib.request.Request(f"{base}{route}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def post_ops(items: Sequence[Mapping[str, Any]], *, base: str = DEFAULT_BASE) -> Any:
    """Replace the ops list. Every item needs id, label, status; persona and detail are optional."""
    for item in items:
        missing = [k for k in ("id", "label", "status") if not item.get(k)]
        if missing:
            raise ValueError(f"ops item {item!r} is missing {missing}")
    return post("/tasks", {"items": list(items)}, base=base)


def say(text: str, *, persona: str | None = None, voice: str | None = None, base: str = DEFAULT_BASE) -> Any:
    body: dict[str, Any] = {"text": text}
    if persona:
        body["persona"] = persona
    if voice:
        body["voice"] = voice
    return post("/say", body, base=base)


def inbox(*, base: str = DEFAULT_BASE) -> list[dict[str, Any]]:
    return list(get("/inbox", base=base).get("items") or [])


def clear_inbox(*, base: str = DEFAULT_BASE) -> Any:
    return post("/inbox/clear", {}, base=base)


def wait_for_inbox(*, base: str = DEFAULT_BASE, max_seconds: float = 600, interval: float = 1.0) -> list[dict[str, Any]]:
    """Block until a directive is queued or the cap passes. Returns the items (possibly none)."""
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        try:
            items = inbox(base=base)
        except (urllib.error.URLError, OSError, ValueError):
            items = []
        if items:
            return items
        time.sleep(interval)
    return []


def cast(*, base: str = DEFAULT_BASE) -> list[dict[str, Any]]:
    return list(get("/cast", base=base).get("seats") or [])
