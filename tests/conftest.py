import re
import sys

import pytest

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _no_cached_core_or_harness_module():
    """`_schema_versions()` does a real `import core` / `import harness`; a
    fake package one test puts on `sys.path` must not leak, via the module
    cache, into a test that runs after it expects the real absence."""
    for name in ("core", "harness"):
        sys.modules.pop(name, None)
    yield
    for name in ("core", "harness"):
        sys.modules.pop(name, None)


def strip_ansi(text: str) -> str:
    """Removes SGR escape sequences, so a help-text assertion reads the same
    whether or not argparse's colorizer is active (Python 3.14 colours help
    output whenever FORCE_COLOR is set, as the cox-launched session does)."""
    return _ANSI.sub("", text)


def pytest_addoption(parser):
    parser.addoption(
        "--regen", action="store_true",
        help="rewrite the snapshots (the help fixtures and go/commands.json) before the run, instead of only comparing",
    )


def pytest_configure(config):
    # A harness build node may run pytest but not the generators, so this is how a build that
    # changes a command refreshes its snapshots: `pytest --regen`, then the snapshot tests compare as usual.
    if not config.getoption("--regen"):
        return
    import test_cli_help_snapshot

    from agent_tools import command_table_json

    test_cli_help_snapshot._write()
    command_table_json.main()
