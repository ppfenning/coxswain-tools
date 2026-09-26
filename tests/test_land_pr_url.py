from agent_tools.cli import _landed_pr, _pr_url


def test_a_url_detail_is_the_pr():
    assert _pr_url("https://github.com/o/r/pull/7") == "https://github.com/o/r/pull/7"


def test_the_local_forge_detail_is_no_pr():
    assert _pr_url("local forge: no pull request") is None


def test_an_empty_detail_is_no_pr():
    assert _pr_url("") is None


def test_a_local_forge_pr_create_leaves_the_land_pr_an_empty_string():
    assert _landed_pr("pr_create", True, "local forge: no pull request", "") == ""
