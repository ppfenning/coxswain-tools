import json
from types import SimpleNamespace

from agent_tools import land, review_pr

URL = "https://github.com/acme/widgets/pull/7"
PROFILE = {
    "harness_dir": "/h", "workspace_dir": "/w", "team": "t", "cartridges_dir": "/c", "provider_profile": "p", "assume": "a",
    "repo_map": {"acme/widgets": "/src/widgets"},
}
RESULT = {
    "verdict": "revise",
    "rationale": "two problems",
    "findings": [
        {"file": "a/b.py", "line": 12, "detail": "mutates its argument", "charter_principle": "A3"},
        {"file": "c.py", "line": 0, "detail": "no test", "charter_principle": "A8"},
    ],
    "checks": [],
}
PR = {"base": {"ref": "main"}, "head": {"sha": "abc123"}}


def stub(uv_lock: bool, result: dict = RESULT):
    calls: list[list[str]] = []

    def runner(argv: list[str]):
        calls.append(argv)
        if argv[:2] == ["gh", "api"] and argv[2].endswith("/pulls/7") and "-H" not in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps(PR))
        if argv[:2] == ["gh", "api"] and argv[2].endswith("/pulls/7"):
            return SimpleNamespace(returncode=0, stdout="diff --git a/a/b.py b/a/b.py\n")
        if argv[:2] == ["gh", "api"] and "contents/uv.lock" in argv[2]:
            return SimpleNamespace(returncode=0 if uv_lock else 1, stdout="")
        if "--result-out" in argv:
            with open(argv[argv.index("--result-out") + 1], "w", encoding="utf-8") as f:
                json.dump(result, f)
        return SimpleNamespace(returncode=0, stdout="")

    return runner, calls


def test_parse_pr_url_reads_owner_repo_and_number_and_rejects_anything_else() -> None:
    assert review_pr.parse_pr_url(URL) == ("acme", "widgets", 7)
    assert review_pr.parse_pr_url("https://github.com/acme/widgets/issues/7") is None


def test_a_repo_with_no_checks_interpreter_is_refused_with_the_land_line_and_nothing_runs(capsys) -> None:
    runner, calls = stub(uv_lock=False)
    assert review_pr.run_review(URL, PROFILE, "review-1", runner) == 2
    assert capsys.readouterr().out.startswith(land.LAUNCH_ERROR + "tests: ")
    assert all(argv[0] == "gh" and argv[1] == "api" for argv in calls)


def test_a_repo_with_an_interpreter_posts_the_review_and_the_inline_comment_the_graph_returned() -> None:
    runner, calls = stub(uv_lock=True)
    assert review_pr.run_review(URL, PROFILE, "review-1", runner) == 0
    fetch, graph, review, comment = calls[-4:]
    assert fetch == ["git", "-C", "/src/widgets", "fetch", "origin", "main"]
    assert graph[2] == "review-diff" and graph[-4:-2] == ["--ref", "origin/main"]
    assert graph[graph.index("--target-repo") + 1] == "/src/widgets"
    assert review == ["gh", "pr", "review", URL, "--request-changes", "--body", "- `c.py`: no test (A8)\n\ntwo problems"]
    assert comment == [
        "gh", "api", "repos/acme/widgets/pulls/7/comments", "-f", "body=mutates its argument (A3)",
        "-f", "commit_id=abc123", "-f", "path=a/b.py", "-F", "line=12", "-f", "side=RIGHT",
    ]


def test_approve_is_approve_and_reject_is_request_changes_with_the_rationale_first() -> None:
    assert review_pr.post_argv("approve", [], "fine", URL, "s") == [["gh", "pr", "review", URL, "--approve", "--body", "fine"]]
    reject = review_pr.post_argv("reject", RESULT["findings"][1:], "wrong approach", URL, "s")
    assert reject == [["gh", "pr", "review", URL, "--request-changes", "--body", "wrong approach\n\n- `c.py`: no test (A8)"]]


def test_no_argv_ever_merges() -> None:
    runner, calls = stub(uv_lock=True)
    review_pr.run_review(URL, PROFILE, "review-1", runner)
    assert not [argv for argv in calls if "merge" in argv]


def test_a_repo_with_no_local_checkout_in_repo_map_is_refused_before_anything_runs(capsys) -> None:
    runner, calls = stub(uv_lock=True)
    assert review_pr.run_review(URL, {**PROFILE, "repo_map": {}}, "review-1", runner) == 2
    assert capsys.readouterr().out == "review: acme/widgets has no mapping in the profile's repo_map\n"
    assert calls == []
