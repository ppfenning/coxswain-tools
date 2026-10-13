"""`route file --repo` writes an absolute checkout path: a bare name resolves beside the workspace, or is refused."""
from pathlib import Path

import pytest

from agent_tools import cli, route
from agent_tools.route import RepoRefused, resolve_repo

ROOT = Path("/repos")


def test_a_bare_name_resolves_to_the_checkout_under_the_repos_root():
    assert resolve_repo("coxswain-tools", ROOT, lambda p: p == ROOT / "coxswain-tools") == "/repos/coxswain-tools"


def test_an_absolute_path_is_kept_as_is():
    assert resolve_repo("/elsewhere/widget", ROOT, lambda p: False) == "/elsewhere/widget"


def test_an_unknown_name_is_refused_naming_the_path_tried():
    refused = resolve_repo("nope", ROOT, lambda p: False)
    assert isinstance(refused, RepoRefused) and "/repos/nope" in refused.message


def test_a_relative_path_is_refused_naming_it():
    assert resolve_repo("acme/widgets", ROOT, lambda p: True) == RepoRefused("acme/widgets", "acme/widgets")


def test_the_edge_reads_a_clone_or_a_worktree_as_a_checkout(tmp_path):
    (tmp_path / "clone" / ".git").mkdir(parents=True)
    (tmp_path / "worktree").mkdir()
    (tmp_path / "worktree" / ".git").write_text("gitdir: /x\n")
    (tmp_path / "plain").mkdir()
    assert [route.is_git_checkout(tmp_path / n) for n in ("clone", "worktree", "plain", "missing")] == [True, True, False, False]


@pytest.fixture
def filed(tmp_path, monkeypatch):
    """A workspace at <tmp>/workspace beside a checkout at <tmp>/widget; `writes` holds each mapping route file writes."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (tmp_path / "widget" / ".git").mkdir(parents=True)
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        f"team: acme\nworkspace_dir: {ws}\nharness_dir: /opt/h\ncartridges_dir: /opt/c\nprovider_profile: /opt/p.yaml\n"
    )
    writes: list[dict] = []
    monkeypatch.setattr(cli, "_write_mapping", lambda mapping, ws, by: writes.append(mapping))
    monkeypatch.setattr(cli, "_sync_filed_items", lambda *_a, **_k: None)

    def file(repo: str) -> int:
        return cli.main(["route", "file", "--profile", str(profile), "--repo", repo, "--title", "Fix the thing", "--intake"])

    return file, writes, tmp_path


def test_route_file_writes_the_resolved_path_for_a_bare_name(filed):
    file, writes, tmp_path = filed
    assert (file("widget"), [f"repo: {tmp_path / 'widget'}\n" in text for text in writes[0].values()]) == (0, [True])


def test_route_file_refuses_an_unknown_name_and_writes_nothing(filed, capsys):
    file, writes, tmp_path = filed
    assert (file("nope"), writes, str(tmp_path / "nope") in capsys.readouterr().out) == (2, [], True)
