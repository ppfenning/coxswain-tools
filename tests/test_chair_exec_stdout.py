import json
import sys

from agent_tools.chair_exec import ArgvResult, run_argv, run_argv_split

PAYLOAD = {"loggedIn": True}
FAKE = (
    "import sys, json\n"
    f"sys.stdout.write(json.dumps({PAYLOAD!r}))\n"
    "sys.stderr.write('warning: stale token\\n')\n"
    "sys.exit(3)\n"
)
ARGV = [sys.executable, "-c", FAKE]


def test_stdout_field_holds_only_the_json() -> None:
    done = run_argv_split(ARGV)
    assert json.loads(done.stdout) == PAYLOAD


def test_stderr_field_holds_the_line() -> None:
    assert run_argv_split(ARGV).stderr == "warning: stale token\n"


def test_returncode_is_the_childs() -> None:
    assert run_argv_split(ARGV).returncode == 3


def test_run_argv_keeps_the_mixed_output() -> None:
    done = run_argv_split(ARGV)
    assert run_argv(ARGV) == (3, json.dumps(PAYLOAD) + "warning: stale token\n")
    assert done.mixed == done.stdout + done.stderr


def test_a_missing_binary_is_127_with_the_same_text_both_ways() -> None:
    argv = ["cox-binary-that-does-not-exist"]
    done = run_argv_split(argv)
    assert (done.returncode, done.stdout) == (127, "")
    assert done.stderr == done.mixed
    assert run_argv(argv) == (127, done.mixed)


def test_a_timeout_is_124_with_the_message_ahead_of_the_partial_output() -> None:
    script = "import sys, time\nsys.stdout.write('out\\n'); sys.stdout.flush()\nsys.stderr.write('err\\n'); sys.stderr.flush()\ntime.sleep(5)\n"
    argv = [sys.executable, "-c", script]
    done = run_argv_split(argv, timeout=1.0)
    assert done.returncode == 124
    assert done.mixed == f"{sys.executable}: timed out after 1.0s\n" + done.stdout + done.stderr
    assert run_argv(argv, timeout=1.0) == (124, done.mixed)
    assert isinstance(done, ArgvResult)
