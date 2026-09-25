import subprocess
from types import SimpleNamespace

from agent_tools import forge, forge_github, forge_local, route


def test_forge_name_defaults_to_local():
    assert forge.forge_name({}) == "local"


def test_forge_name_reads_the_profiles_forge_key():
    assert forge.forge_name({"forge": "github"}) == "github"


def test_forge_for_returns_the_builtin_module():
    assert forge.forge_for("github") is forge_github


def test_forge_for_is_none_for_an_unknown_name():
    assert forge.forge_for("no-such-forge") is None


def test_forge_for_finds_an_entry_point_forge(monkeypatch):
    module = SimpleNamespace(name="acme forge")
    registered = [SimpleNamespace(name="acme", load=lambda: module)]
    monkeypatch.setattr(forge.importlib.metadata, "entry_points", lambda group: registered if group == "coxswain.forges" else [])
    assert forge.forge_for("acme") is module
    assert forge.forge_for("other") is None


def test_parse_profile_accepts_a_forge_line():
    assert route.parse_profile("forge: github\n")["forge"] == "github"


STEP = {"branch": "pr/t", "default_branch": "main"}


def _recording(monkeypatch, answers=()):
    calls, queue = [], list(answers)

    def run(argv, **kwargs):
        calls.append(argv)
        code, out = queue.pop(0) if queue else (0, "")
        return SimpleNamespace(returncode=code, stdout=out, stderr=out)

    monkeypatch.setattr(forge_github.subprocess, "run", run)
    return calls


def test_github_open_pr_passes_head_and_base_when_given(monkeypatch, tmp_path):
    calls = _recording(monkeypatch)
    forge_github.open_pr(tmp_path, "t", "b", head="pr/t", base="main")
    assert calls[0][-4:] == ["--head", "pr/t", "--base", "main"]


def test_github_open_pr_names_neither_ref_by_default(monkeypatch, tmp_path):
    calls = _recording(monkeypatch)
    forge_github.open_pr(tmp_path, "t", "b")
    assert calls[0] == ["gh", "pr", "create", "--title", "t", "--body", "b"]


def _merge_argvs(repo, update):
    return [["gh", "pr", "merge", "pr/t", "--squash", "--delete-branch"],
            ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
            ["git", "-C", str(repo), *update]]


def test_github_merge_names_the_branch_and_pulls_when_on_the_default_branch(monkeypatch, tmp_path):
    calls = _recording(monkeypatch, [(0, "merged"), (0, "main"), (0, "Already up to date.")])
    assert forge_github.merge(tmp_path, STEP) == (True, "merged\nAlready up to date.")
    assert calls == _merge_argvs(tmp_path, ["pull", "--ff-only", "origin", "main"])


def test_github_merge_fetches_the_default_branch_when_on_another_branch(monkeypatch, tmp_path):
    calls = _recording(monkeypatch, [(0, "merged"), (0, "pr/t"), (0, "updated")])
    assert forge_github.merge(tmp_path, STEP) == (True, "merged\nupdated")
    assert calls == _merge_argvs(tmp_path, ["fetch", "origin", "main:main"])


def test_github_merge_fetches_when_head_is_detached(monkeypatch, tmp_path):
    calls = _recording(monkeypatch, [(0, "merged"), (128, "fatal: ref HEAD is not a symbolic ref"), (0, "")])
    assert forge_github.merge(tmp_path, STEP) == (True, "merged")
    assert calls == _merge_argvs(tmp_path, ["fetch", "origin", "main:main"])


def test_github_merge_stays_ok_when_the_local_update_fails(monkeypatch, tmp_path):
    _recording(monkeypatch, [(0, "merged"), (0, "main"), (1, "not fast-forward")])
    ok, detail = forge_github.merge(tmp_path, STEP)
    assert ok is True and detail == "merged\nlocal main not updated: not fast-forward"


def test_github_merge_fails_and_updates_nothing_when_gh_fails(monkeypatch, tmp_path):
    calls = _recording(monkeypatch, [(1, "no such pr")])
    assert forge_github.merge(tmp_path, STEP) == (False, "no such pr")
    assert calls == _merge_argvs(tmp_path, [])[:1]


def test_github_read_checks_rev_parses_the_given_ref(monkeypatch, tmp_path):
    calls = _recording(monkeypatch, [(1, "bad ref")])
    assert forge_github._read_checks(tmp_path, "pr/t") == (False, "bad ref")
    assert calls == [["git", "-C", str(tmp_path), "rev-parse", "pr/t"]]


# --- forge_github.merge against real git; only `gh` is faked, as the host merging pr/t into main ---

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(repo, *args):
    return subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def github_repo(tmp_path):
    """A clone on `pr/t`, one commit ahead of `main`, with both pushed to a bare `origin`."""
    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    git(clone, "commit", "--allow-empty", "-qm", "base")
    git(clone, "remote", "add", "origin", str(origin))
    git(clone, "checkout", "-qb", "pr/t")
    git(clone, "commit", "--allow-empty", "-qm", "task")
    git(clone, "push", "-q", "origin", "main", "pr/t")
    return origin, clone


def fake_gh(monkeypatch, clone, *, then_switch=False):
    """`gh pr merge` lands pr/t on origin's main; `then_switch` also does what gh does to a checkout on the deleted head."""
    real = subprocess.run

    def run(argv, **kw):
        if argv[0] != "gh":
            return real(argv, **kw)
        git(clone, "push", "-q", "origin", "pr/t:main")
        if then_switch:
            git(clone, "checkout", "-q", "main")
            git(clone, "pull", "-q", "--ff-only", "origin", "main")
        return subprocess.CompletedProcess(argv, 0, "merged", "")

    monkeypatch.setattr(forge_github.subprocess, "run", run)


def test_github_merge_off_main_moves_local_main_and_leaves_head_alone(monkeypatch, tmp_path):
    origin, clone = github_repo(tmp_path)
    git(clone, "checkout", "-qb", "elsewhere", "main")
    fake_gh(monkeypatch, clone)
    ok, _ = forge_github.merge(clone, STEP)
    assert ok is True
    assert git(clone, "rev-parse", "main") == git(origin, "rev-parse", "main") == git(clone, "rev-parse", "pr/t")
    assert git(clone, "symbolic-ref", "--short", "HEAD") == "elsewhere"


def test_github_merge_after_gh_switched_to_main_and_pulled_is_a_clean_no_op(monkeypatch, tmp_path):
    origin, clone = github_repo(tmp_path)
    fake_gh(monkeypatch, clone, then_switch=True)
    ok, detail = forge_github.merge(clone, STEP)
    assert (ok, detail) == (True, "merged\nAlready up to date.")
    assert git(clone, "rev-parse", "main") == git(origin, "rev-parse", "main")


def test_github_merge_reports_a_main_checked_out_in_another_worktree_and_stays_ok(monkeypatch, tmp_path):
    origin, clone = github_repo(tmp_path)
    git(clone, "worktree", "add", "-q", str(tmp_path / "wt"), "main")
    before = git(clone, "rev-parse", "main")
    fake_gh(monkeypatch, clone)
    ok, detail = forge_github.merge(clone, STEP)
    assert ok is True
    assert detail.startswith("merged\nlocal main not updated: ") and "checked out at" in detail
    assert git(clone, "rev-parse", "main") == before != git(origin, "rev-parse", "main")


# --- missing_refs: a forge that predates explicit refs is named before a land starts ---

def test_missing_refs_is_empty_for_the_built_in_forges():
    assert forge.missing_refs(forge_github) == forge.missing_refs(forge_local) == []


def test_missing_refs_names_each_keyword_an_old_forge_lacks():
    old = SimpleNamespace(open_pr=lambda repo, title, body: None, wait_checks=lambda repo, timeout_s: None)
    assert forge.missing_refs(old) == ["open_pr(head)", "open_pr(base)", "wait_checks(ref)"]


def test_missing_refs_accepts_a_forge_that_takes_any_keyword():
    loose = SimpleNamespace(open_pr=lambda *a, **kw: None, wait_checks=lambda *a, **kw: None)
    assert forge.missing_refs(loose) == []
