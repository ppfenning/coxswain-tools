"""`route file` writes a repo under the home as `~/...`, and the resolver accepts that form beside the absolute one."""

from pathlib import Path

from agent_tools import route
from agent_tools.route import repo_field, resolve_repo


def _home(tmp_path: Path) -> str:
    return str(tmp_path / "home")


def test_a_repository_under_the_home_is_written_with_the_tilde_prefix(tmp_path):
    home = _home(tmp_path)
    assert repo_field(f"{home}/repos/widget", home) == "~/repos/widget"
    assert repo_field(home, home) == "~"


def test_a_repository_outside_the_home_stays_absolute(tmp_path):
    home = _home(tmp_path)
    assert [repo_field(p, home) for p in ("/opt/elsewhere/widget", "/home/user/widget", f"{home}x/widget")] == [
        "/opt/elsewhere/widget",
        "/home/user/widget",
        f"{home}x/widget",
    ]


def test_a_tilde_value_resolves_to_the_same_directory_as_the_absolute_spelling(tmp_path):
    home = _home(tmp_path)
    root, never = Path("/repos"), lambda p: False
    tilde = resolve_repo("~/repos/widget", root, never, home)
    assert tilde == resolve_repo(f"{home}/repos/widget", root, never, home) == f"{home}/repos/widget"


def test_a_tilde_value_is_refused_without_a_home_and_for_another_user(tmp_path):
    home = _home(tmp_path)
    root, never = Path("/repos"), lambda p: False
    assert [resolve_repo(v, root, never, h) for v, h in (("~/w", None), ("~bob/w", home))] == [
        route.RepoRefused("~/w", "~/w"),
        route.RepoRefused("~bob/w", "~bob/w"),
    ]


def test_an_existing_absolute_record_under_the_home_still_reads_and_resolves(tmp_path):
    home = _home(tmp_path)
    repo = f"{home}/repos/widget"
    text = route.initiative_files("Fix it", "body", repo)["work/fix-it/initiative.md"]
    fields, _ = route.parse_frontmatter(text)
    assert (fields["repo"], resolve_repo(fields["repo"], Path("/repos"), lambda p: False, home)) == (repo, repo)


def test_the_filer_writes_the_tilde_form_for_a_path_under_the_home(tmp_path):
    home = _home(tmp_path)
    files = route.intake_file("Fix it", "body", repo_field(f"{home}/repos/widget", home), "2026-10-16")
    assert "repo: ~/repos/widget\n" in next(iter(files.values()))
