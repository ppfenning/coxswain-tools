"""`cox runs review --pr <url>`: run the graphs `review-diff` entry on a PR and post its verdict as a review."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_tools import land, route

Runner = Callable[[list[str]], Any]

_PR_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?$")
_BARE_CHECKS = [("tests", ["pytest", "-q"])]


def parse_pr_url(url: str) -> tuple[str, str, int] | None:
    m = _PR_URL.match(url)
    return (m[1], m[2], int(m[3])) if m else None


def review_plan(pr: Mapping, repo_facts: Mapping, profile: Mapping, run_id: str) -> list[dict]:
    """`pr` carries `repo` (owner/repo), `target` (its local checkout), `ref`, `diff_path` and `result_path`; a bare-`pytest` fallback means no interpreter is configured."""
    checks = land.checks_argv(dict(repo_facts))
    if checks == _BARE_CHECKS:
        return [{"kind": "refuse", "reason": f"{land.LAUNCH_ERROR}{checks[0][0]}: no interpreter configured for {pr['repo']}"}]
    argv = route.harness_argv(dict(profile), "review-diff", run_id) + [
        "--diff", pr["diff_path"], "--target-repo", pr["target"], "--ref", pr["ref"], "--result-out", pr["result_path"],
    ]
    return [{"kind": "graph", "argv": argv}, {"kind": "post"}]


def _line(finding: Mapping) -> str:
    return f"- `{finding['file']}`: {finding['detail']} ({finding['charter_principle']})"


def post_argv(verdict: str, findings: Sequence[Mapping], rationale: str, url: str, head_sha: str) -> list[list[str]]:
    """The review first, then one inline comment per finding that cites a line; a finding at line 0 goes in the body. Reject leads its body with the rationale."""
    owner, repo, number = parse_pr_url(url) or ("", "", 0)
    general = "\n".join(_line(f) for f in findings if not f["line"])
    body = "\n\n".join(p for p in ((rationale, general) if verdict == "reject" else (general, rationale)) if p)
    flag = "--approve" if verdict == "approve" else "--request-changes"
    inline = [
        ["gh", "api", f"repos/{owner}/{repo}/pulls/{number}/comments", "-f", f"body={f['detail']} ({f['charter_principle']})",
         "-f", f"commit_id={head_sha}", "-f", f"path={f['file']}", "-F", f"line={f['line']}", "-f", "side=RIGHT"]
        for f in findings if f["line"]
    ]
    return [["gh", "pr", "review", url, flag, "--body", body], *inline]


def run_review(url: str, profile: Mapping, run_id: str, runner: Runner) -> int:
    """The edge: fetch, plan, run the graph, post. Exit 2 on a refusal, 1 on a failed call, 0 once posted."""
    parsed = parse_pr_url(url)
    if parsed is None:
        print(f"review: not a github pull request url: {url}")
        return 2
    owner, repo, number = parsed
    # graphs checks the diff out of a local checkout, so the repo must be mapped to one, as `route pull` requires.
    target = (profile.get("repo_map") or {}).get(f"{owner}/{repo}")
    if not target:
        print(f"review: {owner}/{repo} has no mapping in the profile's repo_map")
        return 2
    api = f"repos/{owner}/{repo}/pulls/{number}"
    meta = runner(["gh", "api", api])
    diff = runner(["gh", "api", api, "-H", "Accept: application/vnd.github.diff"])
    if meta.returncode != 0 or diff.returncode != 0:
        print(f"review: could not fetch {url}")
        return 1
    pr = json.loads(meta.stdout)
    uv_lock = runner(["gh", "api", f"repos/{owner}/{repo}/contents/uv.lock?ref={pr['base']['ref']}"]).returncode == 0
    with tempfile.TemporaryDirectory() as tmp:
        diff_path, result_path = Path(tmp) / "pr.diff", Path(tmp) / "result.json"
        diff_path.write_text(diff.stdout, encoding="utf-8")
        plan = review_plan(
            {"repo": f"{owner}/{repo}", "target": target, "ref": f"origin/{pr['base']['ref']}",
             "diff_path": str(diff_path), "result_path": str(result_path)},
            {"uv_lock": uv_lock}, profile, run_id,
        )
        if plan[0]["kind"] == "refuse":
            print(plan[0]["reason"])
            return 2
        if runner(["git", "-C", target, "fetch", "origin", pr["base"]["ref"]]).returncode != 0:
            print(f"review: could not fetch {pr['base']['ref']} into {target}")
            return 1
        if runner(plan[0]["argv"]).returncode != 0:
            print(f"review: the review-diff graph failed for {url}")
            return 1
        result = json.loads(result_path.read_text(encoding="utf-8"))
    posts = post_argv(result["verdict"], result["findings"], result["rationale"], url, pr["head"]["sha"])
    failed = [argv for argv in posts if runner(argv).returncode != 0]
    if failed:
        print(f"review: {len(failed)} of {len(posts)} posts failed")
        return 1
    return 0
