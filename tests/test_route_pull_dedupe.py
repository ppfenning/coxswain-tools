from agent_tools import route, sources

REPOS = {"acme/tools": "tools"}


def _issue(number: int, title: str) -> sources.Candidate:
    return sources.Candidate(title, "body", "acme/tools", f"https://github.com/acme/tools/issues/{number}")


def _plan(candidates, known_issues=frozenset()):
    return route.pull_plan(candidates, frozenset(), REPOS, date="2026-10-16", source="github", known_issues=known_issues)


def test_known_issue_is_planned_nowhere_and_not_refused():
    files, reviews, refusals = _plan([_issue(1647, "Pull dedupe")], frozenset({"1647"}))
    assert (files, reviews, refusals) == ([], [], [])


def test_unknown_issue_plans_one_intake_file():
    files, _, refusals = _plan([_issue(1647, "Pull dedupe")])
    assert refusals == []
    assert [list(f) for f in files] == [["intake/2026-10-16-pull-dedupe.md"]]


def test_other_issue_is_still_planned_beside_a_known_one():
    files, _, refusals = _plan([_issue(1647, "Pull dedupe"), _issue(1648, "Other idea")], frozenset({"1647"}))
    assert refusals == []
    assert [list(f) for f in files] == [["intake/2026-10-16-other-idea.md"]]
