import importlib.metadata
import subprocess
from pathlib import Path

import pytest

from agent_tools.version_info import UNKNOWN, choose_version, package_version

INSTALLED = importlib.metadata.version("coxswain-tools")
DESCRIBED = "v0.37.0-19-gaa15d67"


def test_a_described_version_wins_over_the_installed_one() -> None:
    assert choose_version(DESCRIBED, "0.31.0") == DESCRIBED


def test_a_described_version_is_stripped() -> None:
    assert choose_version(f"  {DESCRIBED}\n", "0.31.0") == DESCRIBED


@pytest.mark.parametrize("described", [None, "", "   \n"])
def test_no_described_version_falls_back_to_installed(described: str | None) -> None:
    assert choose_version(described, "0.31.0") == "0.31.0"


def _checkout(tmp_path: Path, git: bool) -> Path:
    package_dir = tmp_path / "agent_tools"
    package_dir.mkdir()
    if git:
        (tmp_path / ".git").mkdir()
    return package_dir


class FakeRun:
    def __init__(self, stdout: str = "", returncode: int = 0, raises: BaseException | None = None) -> None:
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_a_checkout_reports_git_describe(tmp_path: Path) -> None:
    run = FakeRun(stdout=f"{DESCRIBED}\n")
    assert package_version(_checkout(tmp_path, git=True), run=run) == DESCRIBED
    assert run.calls == [
        (["git", "-C", str(tmp_path), "describe", "--tags"], {"capture_output": True, "text": True, "timeout": 5})
    ]


def test_no_git_entry_never_runs_git_and_reports_the_metadata(tmp_path: Path) -> None:
    run = FakeRun(stdout=f"{DESCRIBED}\n")
    assert package_version(_checkout(tmp_path, git=False), run=run) == INSTALLED
    assert run.calls == []


@pytest.mark.parametrize(
    "run",
    [
        FakeRun(returncode=128, stdout=""),
        FakeRun(raises=FileNotFoundError("git")),
        FakeRun(raises=subprocess.TimeoutExpired(["git"], 5)),
    ],
    ids=["failing", "missing-git", "timeout"],
)
def test_a_git_failure_reports_the_metadata(tmp_path: Path, run: FakeRun) -> None:
    assert package_version(_checkout(tmp_path, git=True), run=run) == INSTALLED


def test_an_uninstalled_package_reports_unknown(tmp_path: Path, monkeypatch) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert package_version(_checkout(tmp_path, git=False), run=FakeRun()) == UNKNOWN
