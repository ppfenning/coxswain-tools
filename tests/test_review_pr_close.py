from agent_tools.review_pr_close import (
    close_comment,
    review_branch,
    review_prs_for_task,
)

SHA = "0123456789abcdef0123456789abcdef01234567"


def test_review_branch_form():
    assert review_branch("i", "a") == "review/i/a"


def test_exact_match_selected():
    prs = [{"number": 1, "head": "review/i/a"}, {"number": 2, "head": "review/i/b"}]
    assert review_prs_for_task("i", "a", prs) == [{"number": 1, "head": "review/i/a"}]


def test_sibling_task_with_same_prefix_not_selected():
    prs = [{"number": 3, "head": "review/i/a-b"}]
    assert review_prs_for_task("i", "a", prs) == []


def test_other_initiative_not_selected():
    prs = [{"number": 4, "head": "review/j/a"}]
    assert review_prs_for_task("i", "a", prs) == []


def test_empty_list_selects_nothing():
    assert review_prs_for_task("i", "a", []) == []


def test_comment_has_full_sha_and_task():
    text = close_comment(SHA, "my-task")
    assert SHA in text
    assert "my-task" in text
