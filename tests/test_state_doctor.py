from pathlib import PurePath

from agent_tools.state_doctor import StateCheck, check_runtime_in_workspace

WS = PurePath("/w/workspace")
ROOT = PurePath("/s/coxswain")


def test_empty_listing_is_ok() -> None:
    assert check_runtime_in_workspace([], WS, ROOT) == StateCheck(True, "no runtime files under the workspace")


def test_records_only_is_ok() -> None:
    assert check_runtime_in_workspace(["abc.json", "abc.md"], WS, ROOT).ok


def test_runtime_files_fail_with_count_examples_and_hint() -> None:
    result = check_runtime_in_workspace(["abc.log", "land.jsonl", "abc.json"], WS, ROOT)
    assert not result.ok
    assert result.message == (
        "2 runtime files under the workspace: abc.log, land.jsonl; run the setup migrate step to move them"
    )


def test_more_than_five_runtime_files_truncate_examples() -> None:
    names = [f"r{i}.log" for i in range(7)]
    result = check_runtime_in_workspace(names, WS, ROOT)
    assert not result.ok
    assert result.message == (
        "7 runtime files under the workspace: r0.log, r1.log, r2.log, r3.log, r4.log and 2 more;"
        " run the setup migrate step to move them"
    )
