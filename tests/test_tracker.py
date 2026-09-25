import subprocess

from agent_tools import cli, route, route_sync_gh, tracker


def test_no_policy_file_and_no_profile_key_gives_none(tmp_path):
    assert tracker.tracker_name({}, tmp_path) == "none"


def test_the_policy_file_wins_over_the_profile(tmp_path):
    (tmp_path / "policy.tracker.json").write_text('{"tracker": "none"}')
    assert tracker.tracker_name({"tracker": "github-projects"}, tmp_path) == "none"


def test_an_unreadable_policy_file_falls_back_to_the_profile(tmp_path):
    (tmp_path / "policy.tracker.json").write_text("{not json")
    assert tracker.tracker_name({"tracker": "github-projects"}, tmp_path) == "github-projects"


def test_a_github_projects_profile_gives_route_sync_gh(tmp_path):
    profile = route.parse_profile("tracker: github-projects\n")
    assert tracker.tracker_for(tracker.tracker_name(profile, tmp_path)) is route_sync_gh


def test_none_is_built_in():
    assert tracker.tracker_for("none") is not None


def test_an_unknown_name_gives_none():
    assert tracker.tracker_for("no-such-tracker") is None


class _EntryPoint:
    def __init__(self, name, target):
        self.name, self._target = name, target

    def load(self):
        return self._target


def _register(monkeypatch, name, target):
    registered = [_EntryPoint(name, target)]
    monkeypatch.setattr(tracker.importlib.metadata, "entry_points",
                        lambda group: registered if group == tracker.ENTRY_POINT_GROUP else [])


def test_a_registered_entry_point_resolves_by_name(monkeypatch):
    adapter = object()
    _register(monkeypatch, "acme-board", adapter)
    assert tracker.tracker_for("acme-board") is adapter


def test_route_sync_refuses_a_registered_tracker_without_calling_gh(tmp_path, monkeypatch, capsys):
    _register(monkeypatch, "acme-board", object())
    _no_subprocess(monkeypatch)
    assert _sync(tmp_path, "tracker: acme-board\n") == 2
    assert "tracker acme-board is installed, but route sync drives only github-projects" in capsys.readouterr().out


def _sync(tmp_path, tracker_line):
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path}\n{tracker_line}")
    return cli.main(["route", "sync", "--dry-run", "--profile", str(profile)])


def _no_subprocess(monkeypatch):
    def fail(argv, *a, **kw):
        raise AssertionError(f"subprocess.run called with {argv}")

    monkeypatch.setattr(subprocess, "run", fail)


def test_route_sync_with_tracker_none_exits_0_without_calling_gh(tmp_path, monkeypatch, capsys):
    _no_subprocess(monkeypatch)
    assert _sync(tmp_path, "") == 0
    assert capsys.readouterr().out == (
        "route sync: tracker is none; set tracker: github-projects in the profile to mirror the work store\n")


def test_route_sync_with_an_unknown_tracker_refuses_without_calling_gh(tmp_path, monkeypatch, capsys):
    _no_subprocess(monkeypatch)
    assert _sync(tmp_path, "tracker: no-such-tracker\n") == 2
    assert "no tracker named no-such-tracker" in capsys.readouterr().out
