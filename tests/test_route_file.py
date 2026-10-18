"""`route file` refuses a repository that is not a git checkout on this machine, before anything is written."""
import pytest

from agent_tools import run_store
from agent_tools.cli import _is_git_checkout_path, _not_a_checkout_message, main


@pytest.fixture(autouse=True)
def _no_gh_sync(monkeypatch):
    monkeypatch.setattr("agent_tools.cli._sync_filed_items", lambda *_a, **_k: None)


@pytest.fixture
def rows(monkeypatch):
    """Every queue row `route file` upserts; no real store is reached."""
    seen = []
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: seen.append(row) or True)
    return seen


def _profile(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        f"team: acme\nworkspace_dir: {ws}\nharness_dir: /opt/h\ncartridges_dir: /opt/c\nprovider_profile: /opt/p.yaml\n"
    )
    return profile, ws


def _written(ws):
    return sorted(p for p in ws.rglob("*") if p.is_file())


def test_the_rule_on_literals(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    clone = tmp_path / "clone"
    (clone / ".git").mkdir(parents=True)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: elsewhere\n")
    home = str(tmp_path)

    assert not _is_git_checkout_path(str(tmp_path / "missing"), home)
    assert not _is_git_checkout_path(str(plain), home)
    assert not _is_git_checkout_path("", home)
    assert _is_git_checkout_path(str(clone), home)
    assert _is_git_checkout_path(str(worktree), home)
    assert _is_git_checkout_path("~/clone", home)
    assert _not_a_checkout_message("~/x") == "route file: refusing, ~/x is not a git checkout on this machine"


@pytest.mark.parametrize("extra", [[], ["--intake"]])
def test_a_missing_folder_is_refused_and_nothing_is_written(tmp_path, capsys, rows, extra):
    profile, ws = _profile(tmp_path)
    missing = tmp_path / "no-such-repo"

    rc = main(["route", "file", "--profile", str(profile), "--repo", str(missing), "--title", "Fix the thing", *extra])

    assert rc == 2
    assert capsys.readouterr().out == f"route file: refusing, {missing} is not a git checkout on this machine\n"
    assert _written(ws) == []
    assert rows == []


def test_a_tilde_value_is_named_as_given_before_expansion(tmp_path, monkeypatch, capsys):
    profile, ws = _profile(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    rc = main(["route", "file", "--profile", str(profile), "--repo", "~/no-such-repo", "--title", "Fix the thing"])

    assert rc == 2
    assert capsys.readouterr().out == "route file: refusing, ~/no-such-repo is not a git checkout on this machine\n"
    assert _written(ws) == []


def test_a_folder_holding_a_git_directory_is_filed(tmp_path, capsys, rows):
    profile, ws = _profile(tmp_path)
    repo = tmp_path / "widget"
    (repo / ".git").mkdir(parents=True)

    rc = main(["route", "file", "--profile", str(profile), "--repo", str(repo), "--title", "Fix the thing", "--intake"])

    assert rc == 0
    assert "refusing" not in capsys.readouterr().out
    [intake] = (ws / "intake").glob("*.md")
    assert f"repo: {repo}" in intake.read_text()
    assert [row["kind"] for row in rows] == ["intake"]


def test_the_frontmatter_repo_gets_the_same_check(tmp_path, capsys, rows):
    profile, ws = _profile(tmp_path)
    (ws / "intake").mkdir()
    intake = ws / "intake" / "2026-10-18-fix-thing.md"
    intake.write_text("---\nid: fix-thing\ntitle: Fix the thing\nrepo: /no/such/repo\n---\n\nDo it.\n")

    rc = main(["route", "file", "--profile", str(profile), "--from-intake", str(intake)])

    assert rc == 2
    assert capsys.readouterr().out == "route file: refusing, /no/such/repo is not a git checkout on this machine\n"
    assert _written(ws) == [intake]
    assert rows == []
