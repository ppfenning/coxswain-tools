from __future__ import annotations

import pytest

from agent_tools import cli, version_info

_VERSION = "v0.37.0-19-gaa15d67"


@pytest.fixture(autouse=True)
def _helper_version(monkeypatch: pytest.MonkeyPatch) -> None:
    # cli imports the helper inside the function, so patching the module attribute reaches both callers.
    monkeypatch.setattr(version_info, "package_version", lambda: _VERSION)


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_prints_the_helper_version(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([flag])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out == f"cox {_VERSION}\n"


def test_package_version_returns_the_helper_version() -> None:
    assert cli._package_version() == _VERSION
