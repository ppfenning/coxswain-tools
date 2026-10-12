from __future__ import annotations

import importlib.metadata
import subprocess
from types import SimpleNamespace

import pytest

from agent_tools.version_info import choose_version, package_version

DESCRIBED = "v0.37.0-19-gaa15d67"


def _metadata_version() -> str:
    try:
        return importlib.metadata.version("coxswain-tools")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def test_described_wins_over_installed():
    assert choose_version(DESCRIBED, "0.31.0") == DESCRIBED


def test_described_is_stripped():
    assert choose_version(f"  {DESCRIBED}\n", "0.31.0") == DESCRIBED


@pytest.mark.parametrize("described", [None, "", "   \n"])
def test_empty_described_falls_back(described):
    assert choose_version(described, "0.31.0") == "0.31.0"


def _checkout(tmp_path, with_git=True):
    package_dir = tmp_path / "agent_tools"
    package_dir.mkdir()
    if with_git:
        (tmp_path / ".git").mkdir()
    return package_dir


def test_checkout_reports_describe_output(tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=DESCRIBED + "\n")

    assert package_version(_checkout(tmp_path), run) == DESCRIBED
    assert calls == [
        (
            ["git", "-C", str(tmp_path), "describe", "--tags"],
            {"capture_output": True, "text": True, "timeout": 5},
        )
    ]


def test_no_git_entry_never_runs_git(tmp_path):
    def run(*args, **kwargs):
        raise AssertionError("git must not run without a .git entry")

    assert package_version(_checkout(tmp_path, with_git=False), run) == _metadata_version()


def test_nonzero_exit_falls_back(tmp_path):
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=128, stdout="")

    assert package_version(_checkout(tmp_path), run) == _metadata_version()


def test_missing_git_falls_back(tmp_path):
    def run(argv, **kwargs):
        raise FileNotFoundError("git")

    assert package_version(_checkout(tmp_path), run) == _metadata_version()


def test_timeout_falls_back(tmp_path):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 5)

    assert package_version(_checkout(tmp_path), run) == _metadata_version()


def test_unknown_when_package_not_installed(tmp_path, monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert package_version(_checkout(tmp_path, with_git=False)) == "unknown"
