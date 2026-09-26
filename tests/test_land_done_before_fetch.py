import json

import pytest

from agent_tools import cli, run_store

RUN = "demo-1"
FETCH_HINT = f"land: {RUN} is a remote run; run cox runs fetch {RUN}, then cox runs land {RUN} again"
DONE_LINE = "land: task is already done; another machine landed it, so nothing is merged"


def _world(tmp_path, monkeypatch, *, work_state, state):
    """A remote run with no fetched marker, work item `t` under initiative `demo`, and a store answering `state`."""
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / f"{RUN}.remote.json").write_text(json.dumps({"host": "box"}))
    item = tmp_path / "work/demo/p1/t.md"
    item.parent.mkdir(parents=True)
    item.write_text("---\nid: t\nstate: approved\n---\nBody.\n", encoding="utf-8")
    provider = tmp_path / "provider.yaml"
    provider.write_text(f"work_state: {work_state}\n", encoding="utf-8")
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"provider_profile: {provider}\n", encoding="utf-8")
    monkeypatch.setattr(run_store, "task_state_of", lambda runs_dir, initiative, task: state)
    return ["runs", "land", RUN, "--task", "t", "--repo", str(tmp_path), "--profile", str(profile), "--runs-dir", str(runs)]


@pytest.mark.parametrize(
    ("work_state", "state", "rc", "line"),
    [("store", "done", 3, DONE_LINE), ("store", "approved", 2, FETCH_HINT), ("files", "done", 2, FETCH_HINT)],
)
def test_the_done_check_comes_before_the_fetch_refusal_only_under_store(tmp_path, monkeypatch, capsys, work_state, state, rc, line):
    argv = _world(tmp_path, monkeypatch, work_state=work_state, state=state)
    assert cli.main(argv) == rc
    assert capsys.readouterr().out.strip() == line
