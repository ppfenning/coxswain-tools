from agent_tools.card_branch import card_branch, card_mode, task_commit_message


def test_branch_differs_per_repository() -> None:
    assert card_branch("init-a", "core") == "card/init-a/core"
    assert card_branch("init-a", "core") != card_branch("init-a", "tools")


def test_commit_message_carries_task_id_on_its_own_line() -> None:
    lines = task_commit_message("T-1", "Add thing").split("\n")
    assert lines == ["Add thing", "", "Task: T-1"]


def test_card_mode_false_for_shipping_initiative() -> None:
    assert card_mode("builds-on-one-card-branch-and-opens-one-pr-per", True) is False


def test_card_mode_false_when_launched_before_ship() -> None:
    assert card_mode("init-a", False) is False


def test_card_mode_true_otherwise() -> None:
    assert card_mode("init-a", True) is True
