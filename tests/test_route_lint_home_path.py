"""Route lint: a tilde path is inside a tilde repo, and an absolute home path is refused."""

from agent_tools import route

HOME = "/".join(["", "home", "someone"])
OTHER_HOME = "/".join(["", "Users", "elsewhere"])
TILDE_REPO = "/".join(["~", "repos", "x"])
ABS_REPO = "/".join([HOME, "repos", "x"])


def _item(body="do it", surfaces=()):
    return {"task": "t1", "body": body, "surfaces": list(surfaces)}


def _rules(problems):
    return [p.rule for p in problems]


def test_tilde_surface_under_tilde_repo_has_no_reach_problem():
    items = [_item(surfaces=["/".join([TILDE_REPO, "a.py"])])]
    assert "reach" not in _rules(route.lint_items(items, TILDE_REPO, (), home=HOME))


def test_absolute_path_in_body_is_one_home_path_problem_with_tilde_fix():
    path = "/".join([ABS_REPO, "a.py"])
    problems = route.lint_items([_item(body=f"edit {path} now")], TILDE_REPO, (), home=HOME)
    assert _rules(problems) == ["home_path"]
    assert path in problems[0].detail
    assert "/".join([TILDE_REPO, "a.py"]) in problems[0].fix


def test_another_machines_home_path_is_flagged():
    path = "/".join([OTHER_HOME, "repos", "x", "a.py"])
    problems = route.lint_items([_item(surfaces=[path])], TILDE_REPO, (), home=HOME)
    assert "home_path" in _rules(problems)


def test_relative_path_has_no_problem():
    items = [_item(body="edit agent_tools/route.py", surfaces=["agent_tools/route.py"])]
    assert route.lint_items(items, TILDE_REPO, (), home=HOME) == []


def test_without_home_the_result_is_unchanged():
    items = [_item(body="edit " + "/".join([ABS_REPO, "a.py"]), surfaces=["/".join([TILDE_REPO, "a.py"])])]
    without = route.lint_items(items, ABS_REPO, ())
    assert without == route.lint_items(items, ABS_REPO, (), home=None)
    assert "home_path" not in _rules(without)
