"""Serve a visual plan through the local bridge and open it in Brave. The bridge stays up in the background."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

__all__ = ["serve", "check"]

BRIDGE = ["npx", "-y", "@agent-native/core@latest", "plan", "local"]


def check(plan_dir: Path | str) -> str:
    return subprocess.run([*BRIDGE, "check", "--dir", str(plan_dir)], capture_output=True, text=True).stdout


def serve(plan_dir: Path | str, *, kind: str = "plan", open_browser: bool = True, wait: float = 90) -> str:
    """Start the bridge detached, wait for `.plan-url`, open it in Brave. Returns the URL."""
    plan_dir = Path(plan_dir)
    url_file = plan_dir / ".plan-url"
    url_file.unlink(missing_ok=True)
    log = open(plan_dir / ".plan-serve.log", "w")
    subprocess.Popen([*BRIDGE, "serve", "--dir", str(plan_dir), "--kind", kind],
                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and not (url_file.exists() and url_file.read_text().strip()):
        time.sleep(1)
    url = url_file.read_text().strip() if url_file.exists() else ""
    if not url:
        raise RuntimeError(f"the plan bridge did not publish a URL within {wait}s; see {plan_dir / '.plan-serve.log'}")
    if open_browser:
        browser = os.environ.get("AGENT_TOOLS_BROWSER", "brave-browser")
        subprocess.Popen([browser, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return url
