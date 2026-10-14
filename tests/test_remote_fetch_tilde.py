from agent_tools.lane_hosts import LaneHost
from agent_tools.remote_fetch import FetchError, chair_repo_path, fetch_run, host_repo_path

CHAIR_HOME = "/".join(["", "home", "chair"])
OTHER_HOME = "/".join(["", "home", "other"])
CHAIR_WS = "/".join(["", "srv", "chair-ws"])
HOST_WS = "/".join(["", "srv", "host-ws"])
CHAIR_REPO = "/".join([CHAIR_HOME, "code", "x"])


def test_a_tilde_repo_maps_under_the_chair_home_and_to_the_home_relative_host_form():
    chair = chair_repo_path(CHAIR_WS, HOST_WS, "~/code/x", CHAIR_HOME)
    assert chair == CHAIR_REPO
    assert host_repo_path(CHAIR_WS, HOST_WS, chair, CHAIR_HOME) == "code/x"


def test_a_repo_under_another_users_home_maps_the_same_way():
    chair = chair_repo_path(CHAIR_WS, HOST_WS, "/".join([OTHER_HOME, "code", "x"]), CHAIR_HOME)
    assert chair == CHAIR_REPO
    assert host_repo_path(CHAIR_WS, HOST_WS, chair, CHAIR_HOME) == "code/x"


def test_a_repo_under_the_host_workspace_still_maps_under_the_chair_workspace():
    recorded = "/".join([HOST_WS, "repo"])
    assert chair_repo_path(CHAIR_WS, HOST_WS, recorded, CHAIR_HOME) == "/".join([CHAIR_WS, "repo"])
    assert chair_repo_path(CHAIR_WS, HOST_WS, recorded) == "/".join([CHAIR_WS, "repo"])
    assert host_repo_path(CHAIR_WS, HOST_WS, "/".join([CHAIR_WS, "repo"]), CHAIR_HOME) == "/".join([HOST_WS, "repo"])


def test_without_a_home_a_tilde_path_is_left_as_recorded():
    assert chair_repo_path(CHAIR_WS, HOST_WS, "~/code/x") == "~/code/x"


def test_fetch_run_refuses_an_empty_home_before_running_anything(tmp_path):
    calls: list[list[str]] = []
    result = fetch_run(
        LaneHost("h", "u@h", HOST_WS), "r1", tmp_path / "runs", lambda _: ["~/code/x"],
        lambda argv: calls.append(argv) or 0, lease_released=True, ended_at="t", home="",
    )
    assert isinstance(result, FetchError) and result.step == "home"
    assert calls == []


def test_fetch_run_fetches_into_the_chair_path_from_the_home_relative_host_location(tmp_path):
    calls: list[list[str]] = []

    def run_cmd(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    result = fetch_run(
        LaneHost("h", "u@h", HOST_WS), "r1", tmp_path / "runs", lambda _: ["~/code/x"], run_cmd,
        lease_released=True, ended_at="t", store_records=lambda *_: [], home=CHAIR_HOME,
    )
    assert result == (CHAIR_REPO,)
    fetches = [argv for argv in calls if argv[:4] == ["git", "-C", CHAIR_REPO, "fetch"]]
    assert len(fetches) == 1
    assert fetches[0][4] == "u@h:code/x"


def test_fetch_run_scopes_a_tilde_task_record_to_the_chair_repo_and_probes_its_branch(tmp_path):
    calls: list[list[str]] = []
    record = tmp_path / "runs" / "r1" / "tasks" / "p1" / "t1.json"
    record.parent.mkdir(parents=True)
    record.write_text('{"task": "t1", "repo": "~/code/x"}')

    def run_cmd(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    fetch_run(
        LaneHost("h", "u@h", HOST_WS), "r1", tmp_path / "runs", lambda _: ["~/code/x"], run_cmd,
        lease_released=True, ended_at="t", home=CHAIR_HOME,
    )
    assert ["git", "-C", CHAIR_REPO, "rev-parse", "--verify", "--quiet", "refs/heads/agents/r1/t1"] in calls


def test_a_workspace_under_a_home_maps_to_the_home_relative_host_form():
    chair_ws = "/".join([CHAIR_HOME, "ws"])
    host_ws = "/".join(["", "home", "lane", "ws"])
    assert host_repo_path(chair_ws, host_ws, "/".join([chair_ws, "repo"]), CHAIR_HOME) == "ws/repo"
