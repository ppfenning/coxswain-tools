from pathlib import Path

from agent_tools import chair_facts, cli

OTHER_HOME = "/home/user"  # the neutral stand-in home that check_clean allows; never this machine's home


def _initiative(ws: Path, name: str, repo: str | None) -> Path:
    path = ws / "work" / name / "initiative.md"
    path.parent.mkdir(parents=True)
    line = f"repo: {repo}\n" if repo is not None else ""
    path.write_text(f"---\nid: {name}\n{line}---\n\nbody\n", encoding="utf-8")
    return path


def _files(*paths: Path) -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in paths}


def test_a_tilde_repo_reads_back_under_the_home_from_both_readers(tmp_path):
    home = str(tmp_path / "home")
    path = _initiative(tmp_path, "x", "~/repos/x")
    assert chair_facts.read_initiative_repos(tmp_path, home) == {"x": f"{home}/repos/x"}
    assert cli._initiative_repos(_files(path), home) == {"x": f"{home}/repos/x"}


def test_a_repo_under_another_users_home_is_re_rooted_under_the_home(tmp_path):
    home = str(tmp_path / "home")
    path = _initiative(tmp_path, "x", f"{OTHER_HOME}/repos/x")
    assert chair_facts.read_initiative_repos(tmp_path, home) == {"x": f"{home}/repos/x"}
    assert cli._initiative_repos(_files(path), home) == {"x": f"{home}/repos/x"}


def test_repo_for_expands_the_actions_own_repo_and_the_initiative_fallback(tmp_path):
    home = str(tmp_path / "home")
    _initiative(tmp_path, "x", f"{OTHER_HOME}/repos/x")
    _initiative(tmp_path, "bare", None)
    repo_for = cli._repo_for(tmp_path, home)
    assert repo_for({"repo": "~/repos/own", "initiative": "x"}) == f"{home}/repos/own"
    assert repo_for({"initiative": "x"}) == f"{home}/repos/x"
    assert repo_for({"initiative": "bare"}) == ""


def test_an_initiative_with_no_repo_is_left_out_of_both_readers(tmp_path):
    home = str(tmp_path / "home")
    path = _initiative(tmp_path, "bare", None)
    assert chair_facts.read_initiative_repos(tmp_path, home) == {}
    assert cli._initiative_repos(_files(path), home) == {}
