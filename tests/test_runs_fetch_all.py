import json

import pytest

from agent_tools import cli, remote_fetch, remote_lane
from agent_tools.cli import main


def test_unfetched_keeps_the_order_and_drops_runs_with_a_tasks_directory():
    assert remote_lane.unfetched(["a", "b", "c"], {"b"}) == ["a", "c"]


def _world(tmp_path, monkeypatch, runs, fetched=()):
    """A chair runs dir with a `.remote.json` per run, a local tasks directory for `fetched`, and a fetch edge that records its calls."""
    ws = tmp_path / "chair"
    runs_dir = ws / "runs"
    runs_dir.mkdir(parents=True)
    for run in runs:
        (runs_dir / f"{run}.remote.json").write_text(json.dumps({"host": "box", "launched_at": "2026-09-25T04:00:00Z"}))
    for run in fetched:
        (runs_dir / run / "tasks").mkdir(parents=True)
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        f"team: acme\nharness_dir: /opt/harness\nworkspace_dir: {ws}\n"
        "cartridges_dir: /opt/cartridges\nprovider_profile: /opt/providers/acme.yaml\n"
        "lane_hosts:\n  - {name: box, ssh: me@box, workspace_dir: /remote}\n"
    )
    calls = []

    def fake_fetch(host, run, *_args, **_kwargs):
        calls.append(run)
        if run.startswith("live"):
            return remote_fetch.FetchError("refuse", "lease is held")
        if run.startswith("bad"):
            return remote_fetch.FetchError("rsync", "boom")
        return ["/chair/repo"]

    monkeypatch.setattr(cli.remote_fetch, "fetch_run", fake_fetch)
    monkeypatch.setattr(cli, "_remote_edge", lambda cwd: (None, None))
    monkeypatch.setattr(cli, "_remote_fetch_facts", lambda runs_dir, run: (True, "2026-09-25T05:00:00Z"))
    return profile, calls


def test_all_fetches_the_ended_run_skips_the_live_one_and_leaves_the_fetched_one(tmp_path, monkeypatch, capsys):
    profile, calls = _world(tmp_path, monkeypatch, ["done-1", "live-1", "have-1"], fetched=["have-1"])
    rc = main(["runs", "fetch", "--all", "--profile", str(profile)])
    assert rc == 0
    assert calls == ["done-1", "live-1"]
    assert capsys.readouterr().out.splitlines() == ["done-1: fetched from box", "live-1: still live on box"]


def test_all_exits_2_when_a_fetch_fails_and_still_fetches_the_rest(tmp_path, monkeypatch, capsys):
    profile, calls = _world(tmp_path, monkeypatch, ["bad-1", "done-1"])
    rc = main(["runs", "fetch", "--all", "--profile", str(profile)])
    assert rc == 2
    assert calls == ["bad-1", "done-1"]
    assert capsys.readouterr().out.splitlines() == ["bad-1: fetch: rsync: boom", "done-1: fetched from box"]


def test_all_says_nothing_to_fetch_when_every_run_is_fetched(tmp_path, monkeypatch, capsys):
    profile, calls = _world(tmp_path, monkeypatch, ["have-1"], fetched=["have-1"])
    assert main(["runs", "fetch", "--all", "--profile", str(profile)]) == 0
    assert calls == []
    assert capsys.readouterr().out.strip() == "nothing to fetch"


@pytest.mark.parametrize("argv", [["runs", "fetch"], ["runs", "fetch", "x", "--all"]])
def test_neither_or_both_of_run_id_and_all_is_an_argparse_error(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert "usage: cox runs fetch" in capsys.readouterr().err
