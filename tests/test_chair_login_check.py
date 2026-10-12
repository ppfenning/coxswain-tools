import json

from agent_tools import chair_login_check
from agent_tools.chair_exec import ArgvResult
from agent_tools.chair_login_check import check_login_on_host

NOW = "2026-09-28T00:00:00+00:00"
SSH_BOUND = ["ssh", "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3"]
LOGGED_IN = '{"loggedIn": true}'
LOGGED_OUT = '{"loggedIn": false}'
EXPIRED = json.dumps({"claudeAiOauth": {"refreshTokenExpiresAt": 1_000_000_000_000}})
VALID = json.dumps({"claudeAiOauth": {"refreshTokenExpiresAt": 4_102_444_800_000}})


def _reply(code: int, stdout: str = "", stderr: str = "") -> ArgvResult:
    return ArgvResult(code, stdout, stderr, stdout + stderr)


def _versions_arg(argv: list[str]) -> dict:
    return json.loads(argv[argv.index("--versions") + 1])


def _recording_cli(captured: list) -> object:
    def cli_run(argv: list[str]) -> dict:
        captured.append(_versions_arg(argv))
        return {"versions_json": captured[-1]}

    return cli_run


def _fake(statuses: list[ArgvResult], credentials: ArgvResult | None = None, renew: ArgvResult | None = None):
    """A runner that answers status calls from `statuses` in order, and records every argv."""
    calls: list = []
    queue = list(statuses)

    def run(argv: list[str]) -> ArgvResult:
        calls.append(argv)
        text = argv[-1] if argv[0] == "ssh" else " ".join(argv)
        if "auth status" in text:
            return queue.pop(0)
        if "-p ok" in text:
            return renew or _reply(0, "ok")
        if "Claude Code-credentials" in text:
            return credentials or _reply(1, "", "no such file")
        raise AssertionError(f"unexpected argv {argv}")

    return run, calls


def _renewals(calls: list) -> int:
    return sum(1 for argv in calls if "-p ok" in (argv[-1] if argv[0] == "ssh" else " ".join(argv)))


def _check(statuses, versions=None, **fake) -> tuple[dict, list]:
    run, calls = _fake(statuses, **fake)
    captured: list = []
    check_login_on_host("shed", "user@shed", versions or {}, NOW, run, _recording_cli(captured))
    return captured[0], calls


def test_a_logged_in_host_merges_login_ok_true_and_now_and_keeps_existing_keys() -> None:
    run, calls = _fake([_reply(0, LOGGED_IN)])

    def cli_run(argv: list[str]) -> dict:
        return {"name": "shed", "versions_json": _versions_arg(argv)}

    row = check_login_on_host("shed", "user@shed", {"cox": "1.2.3"}, NOW, run, cli_run)

    versions = row["versions_json"]
    assert versions["login_ok"] is True
    assert versions["login_checked_at"] == NOW
    assert versions["login_failures"] == 0
    assert versions["cox"] == "1.2.3"
    assert calls == [[*SSH_BOUND, "user@shed", "claude auth status"]]


def test_a_stderr_line_before_the_json_still_parses_as_logged_in() -> None:
    versions, _ = _check([_reply(0, LOGGED_IN, "warning: update available\n")])

    assert versions["login_ok"] is True
    assert versions["login_failures"] == 0


def test_the_loop_s_own_host_is_checked_locally_with_no_ssh(monkeypatch) -> None:
    monkeypatch.setattr(chair_login_check.socket, "gethostname", lambda: "omarchy")
    run, calls = _fake([_reply(0, LOGGED_IN)])
    captured: list = []

    check_login_on_host("omarchy", "user@omarchy", {}, NOW, run, _recording_cli(captured))

    assert calls == [["claude", "auth", "status"]]
    assert captured[0] == {"login_ok": True, "login_checked_at": NOW, "check": "claude_auth", "login_failures": 0}


def test_a_different_host_than_the_loop_s_own_is_still_checked_over_ssh(monkeypatch) -> None:
    monkeypatch.setattr(chair_login_check.socket, "gethostname", lambda: "omarchy")
    run, calls = _fake([_reply(0, LOGGED_IN)])

    check_login_on_host("shed", "user@shed", {}, NOW, run, _recording_cli([]))

    assert calls == [[*SSH_BOUND, "user@shed", "claude auth status"]]


def test_a_claude_code_runner_call_builds_the_same_argv_and_never_calls_env_check_argv(monkeypatch) -> None:
    run, calls = _fake([_reply(0, LOGGED_IN)])
    captured: list = []

    def _boom(_var: str) -> list[str]:
        raise AssertionError("env_check_argv must not be called for the claude-code runner")

    monkeypatch.setattr(chair_login_check, "env_check_argv", _boom)

    check_login_on_host("shed", "user@shed", {}, NOW, run, _recording_cli(captured), runner="claude-code")

    assert calls == [[*SSH_BOUND, "user@shed", "claude auth status"]]
    assert captured[0] == {"login_ok": True, "login_checked_at": NOW, "check": "claude_auth", "login_failures": 0}


def test_one_unreadable_reply_stores_none_with_a_reason_and_does_not_block() -> None:
    versions, calls = _check([_reply(1, "", "connection refused")])

    assert versions["login_ok"] is None
    assert versions["login_failures"] == 1
    assert versions["reason"] == "exit 1: connection refused"
    assert _renewals(calls) == 0
    assert len(calls) == 1


def test_a_second_unreadable_reply_blocks_the_host() -> None:
    versions, _ = _check([_reply(1, "", "connection refused")], {"login_ok": None, "login_failures": 1})

    assert versions["login_ok"] is False
    assert versions["login_failures"] == 2


def test_unreadable_credentials_renew_once_and_a_failed_renewal_does_not_block_the_first_time() -> None:
    versions, calls = _check([_reply(1, LOGGED_OUT), _reply(1, LOGGED_OUT)])

    assert _renewals(calls) == 1
    assert versions["login_ok"] is None
    assert versions["login_failures"] == 1
    assert versions["reason"] == f"exit 1: {LOGGED_OUT}"


def test_a_renewal_that_works_is_logged_in_with_one_p_call_and_clears_the_failure() -> None:
    stored = {"login_ok": None, "login_failures": 1, "reason": "exit 1: stale"}
    versions, calls = _check([_reply(1, LOGGED_OUT), _reply(0, LOGGED_IN)], stored, credentials=_reply(0, VALID))

    assert _renewals(calls) == 1
    assert versions["login_ok"] is True
    assert versions["login_failures"] == 0
    assert "reason" not in versions


def test_an_expired_refresh_token_is_false_at_once_and_never_renews() -> None:
    versions, calls = _check([_reply(1, LOGGED_OUT)], credentials=_reply(0, EXPIRED))

    assert _renewals(calls) == 0
    assert versions["login_ok"] is False
    assert versions["login_failures"] == 1
    assert versions["reason"] == f"exit 1: {LOGGED_OUT}"


def test_credentials_are_read_with_one_script_that_names_both_stores() -> None:
    _, calls = _check([_reply(1, LOGGED_OUT), _reply(0, LOGGED_IN)], credentials=_reply(0, VALID))

    script = next(argv for argv in calls if "Claude Code-credentials" in argv[-1])[-1]
    assert "security find-generic-password" in script
    assert "~/.claude/.credentials.json" in script


def test_the_reason_keeps_only_the_first_200_characters_of_output() -> None:
    versions, _ = _check([_reply(7, "x" * 500)])

    assert versions["reason"] == "exit 7: " + "x" * 200


def test_refresh_expiry_reads_milliseconds_seconds_and_iso_and_rejects_junk() -> None:
    def blob(value: object) -> str:
        return json.dumps({"claudeAiOauth": {"refreshTokenExpiresAt": value}})

    from_ms = chair_login_check._refresh_expiry(blob(4_102_444_800_000))
    assert from_ms is not None and from_ms.year == 2100
    from_s = chair_login_check._refresh_expiry(blob(4_102_444_800))
    assert from_s == from_ms
    from_iso = chair_login_check._refresh_expiry(blob("2100-01-01T00:00:00Z"))
    assert from_iso == from_ms
    assert chair_login_check._refresh_expiry("not json") is None
    assert chair_login_check._refresh_expiry(blob(True)) is None
    assert chair_login_check._refresh_expiry("{}") is None
    assert chair_login_check._refresh_expired(None, NOW) is False


def test_login_ok_keeps_its_tuple_runner_contract() -> None:
    assert chair_login_check._login_ok("shed", "u@shed", lambda argv: (0, LOGGED_IN)) is True
    assert chair_login_check._login_ok("shed", "u@shed", lambda argv: (1, LOGGED_OUT)) is False
    assert chair_login_check._login_ok("shed", "u@shed", lambda argv: (1, "refused")) is None


def test_an_env_vars_runner_with_every_variable_present_is_logged_in() -> None:
    def ssh_run(argv: list[str]) -> ArgvResult:
        return _reply(0, "some-secret-value\n")

    row = check_login_on_host(
        "shed", "user@shed", {}, NOW, ssh_run, lambda argv: {"versions_json": _versions_arg(argv)},
        runner="litellm", env_names=("MY_API_KEY",),
    )

    versions = row["versions_json"]
    assert versions["login_ok"] is True
    assert versions["check"] == "env_vars"
    assert versions["login_checked_at"] == NOW
    assert "reason" not in versions
    assert "login_failures" not in versions


def test_an_env_vars_runner_with_a_missing_variable_names_it_in_the_reason() -> None:
    row = check_login_on_host(
        "shed", "user@shed", {}, NOW, lambda argv: _reply(0, ""), lambda argv: {"versions_json": _versions_arg(argv)},
        runner="litellm", env_names=("MY_API_KEY",),
    )

    versions = row["versions_json"]
    assert versions["login_ok"] is False
    assert versions["check"] == "env_vars"
    assert "MY_API_KEY" in versions["reason"]


def test_an_env_vars_runner_on_an_unreachable_host_is_not_logged_in() -> None:
    row = check_login_on_host(
        "shed", "user@shed", {}, NOW,
        lambda argv: _reply(255, "", "ssh: connect to host x: Connection refused"),
        lambda argv: {"versions_json": _versions_arg(argv)},
        runner="litellm", env_names=("MY_API_KEY",),
    )

    assert row["versions_json"]["login_ok"] is False


def test_an_env_vars_runner_with_no_env_names_is_not_logged_in_and_runs_nothing() -> None:
    calls: list = []

    def ssh_run(argv: list[str]) -> ArgvResult:
        calls.append(argv)
        return _reply(0, "unused")

    row = check_login_on_host(
        "shed", "user@shed", {}, NOW, ssh_run, lambda argv: {"versions_json": _versions_arg(argv)}, runner="litellm"
    )

    assert row["versions_json"] == {
        "login_ok": False,
        "login_checked_at": NOW,
        "check": "env_vars",
        "reason": "no auth_env or endpoint_env configured",
    }
    assert calls == []


def test_a_failed_check_stores_the_login_url_from_the_ssh_output() -> None:
    output = "# Tailscale SSH requires an additional check.\n# To authenticate, visit: https://login.tailscale.com/a/abc123\n"
    versions, _ = _check([_reply(255, "", output)])

    assert versions["login_ok"] is None
    assert versions["login_url"] == "https://login.tailscale.com/a/abc123"


def test_a_failed_check_with_no_url_stores_no_login_url_key() -> None:
    versions, _ = _check([_reply(1, "", "connection refused")])

    assert "login_url" not in versions


def test_a_passing_check_clears_a_stored_login_url() -> None:
    stored = {"login_ok": False, "login_url": "https://login.tailscale.com/a/abc123"}
    versions, _ = _check([_reply(0, LOGGED_IN)], stored)

    assert versions == {"login_ok": True, "login_checked_at": NOW, "check": "claude_auth", "login_failures": 0}
