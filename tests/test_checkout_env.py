import copy

from agent_tools.checkout_env import (
    CHECKOUT_ENV_KEY,
    SYNC_COMMAND,
    blocks_launch,
    env_verdict,
    host_env_verdict,
    sync_instruction,
)


def test_key_value():
    assert CHECKOUT_ENV_KEY == "checkout_env"


def test_equal_hashes_are_fresh():
    assert env_verdict("abc", "abc") == "fresh"


def test_unequal_hashes_are_stale():
    assert env_verdict("abc", "def") == "stale"


def test_synced_none_is_never_synced():
    assert env_verdict(None, "def") == "never_synced"


def test_no_lockfile_is_fresh():
    assert env_verdict(None, None) == "fresh"
    assert env_verdict("abc", None) == "fresh"


def test_absent_key_is_unknown():
    assert host_env_verdict({"git": "2.40"}, "coxswain") == "unknown"


def test_absent_repo_is_unknown():
    versions = {CHECKOUT_ENV_KEY: {"other": {"lock_hash": "a", "synced_hash": "a"}}}
    assert host_env_verdict(versions, "coxswain") == "unknown"


def test_malformed_key_value_is_unknown():
    assert host_env_verdict({CHECKOUT_ENV_KEY: None}, "coxswain") == "unknown"


def test_malformed_repo_entry_is_unknown():
    assert host_env_verdict({CHECKOUT_ENV_KEY: {"coxswain": "x"}}, "coxswain") == "unknown"


def test_entry_without_synced_hash_is_never_synced():
    assert host_env_verdict({CHECKOUT_ENV_KEY: {"coxswain": {"lock_hash": "b"}}}, "coxswain") == "never_synced"


def test_sync_command_keeps_extras_and_tolerates_a_lagging_lock():
    assert SYNC_COMMAND == "uv sync --frozen --all-extras"


def test_present_entry_is_judged_and_not_mutated():
    versions = {CHECKOUT_ENV_KEY: {"coxswain": {"lock_hash": "b", "synced_hash": "a"}}}
    before = copy.deepcopy(versions)
    assert host_env_verdict(versions, "coxswain") == "stale"
    assert versions == before


def test_blocks_launch_per_verdict():
    assert blocks_launch("stale") is True
    assert blocks_launch("never_synced") is True
    assert blocks_launch("fresh") is False
    assert blocks_launch("unknown") is False


def test_sync_instruction_names_host_repo_and_command():
    text = sync_instruction("ocean", "coxswain")
    assert "ocean" in text
    assert "coxswain" in text
    assert SYNC_COMMAND in text
    assert "\n" not in text
