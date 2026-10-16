from agent_tools.home import Locations, Refused, resolve, resolve_here, write_home_record

U = "/home/user"
DEFAULT_HOME = "/home/user/.local/share/coxswain"
DEFAULT_STATE = "/home/user/.local/state/coxswain"
DEFAULT_PROFILE = "/home/user/.config/agent-tools/profile.yaml"


def home(result):
    assert isinstance(result, Locations)
    return result.home


def test_default_layout():
    got = resolve({}, U, "linux")
    assert got == Locations(
        home=DEFAULT_HOME,
        installs=DEFAULT_HOME + "/installs",
        workspaces=DEFAULT_HOME + "/workspaces",
        config="/home/user/.config",
        state=DEFAULT_STATE,
        profile=DEFAULT_PROFILE,
        record="/home/user/.config/agent-tools/home",
    )


def test_override_set():
    assert home(resolve({"COXSWAIN_HOME": "/mnt/big/cox"}, U, "linux")) == "/mnt/big/cox"


def test_override_unset_uses_default():
    assert home(resolve({}, U, "linux")) == DEFAULT_HOME


def test_empty_override_counts_as_unset():
    assert home(resolve({"COXSWAIN_HOME": ""}, U, "linux")) == DEFAULT_HOME


def test_relative_override_refused():
    got = resolve({"COXSWAIN_HOME": "rel/cox"}, U, "linux")
    assert got == Refused("COXSWAIN_HOME must be an absolute path, got 'rel/cox'")


def test_relative_flag_refused():
    assert isinstance(resolve({}, U, "linux", flag="rel"), Refused)


def test_flag_beats_variable():
    assert home(resolve({"COXSWAIN_HOME": "/a"}, U, "linux", flag="/b")) == "/b"


def test_variable_beats_record():
    assert home(resolve({"COXSWAIN_HOME": "/a"}, U, "linux", record="/r")) == "/a"


def test_record_beats_xdg():
    assert home(resolve({"XDG_DATA_HOME": "/x"}, U, "linux", record="/r")) == "/r"


def test_relative_or_empty_record_is_unset():
    assert home(resolve({}, U, "linux", record="rel")) == DEFAULT_HOME
    assert home(resolve({}, U, "linux", record="  ")) == DEFAULT_HOME


def test_xdg_data_home_set():
    assert home(resolve({"XDG_DATA_HOME": "/x/data"}, U, "linux")) == "/x/data/coxswain"


def test_xdg_data_home_relative_or_empty_is_unset():
    assert home(resolve({"XDG_DATA_HOME": "data"}, U, "linux")) == DEFAULT_HOME
    assert home(resolve({"XDG_DATA_HOME": ""}, U, "linux")) == DEFAULT_HOME


def test_xdg_state_home_set_and_unset():
    assert resolve({"XDG_STATE_HOME": "/x/state"}, U, "linux").state == "/x/state/coxswain"
    assert resolve({"XDG_STATE_HOME": "state"}, U, "linux").state == DEFAULT_STATE
    assert resolve({"XDG_STATE_HOME": ""}, U, "linux").state == DEFAULT_STATE


def test_config_and_state_ignore_other_variables():
    env = {"XDG_CONFIG_HOME": "/x/cfg", "COXSWAIN_HOME": "/mnt/cox"}
    got = resolve(env, U, "linux")
    assert got.config == "/home/user/.config"
    assert got.state == DEFAULT_STATE
    assert got.record == "/home/user/.config/agent-tools/home"


def test_profile_override_wins():
    assert resolve({"AGENT_TOOLS_PROFILE": "/p/profile.yaml"}, U, "linux").profile == "/p/profile.yaml"
    assert resolve({}, U, "linux").profile == DEFAULT_PROFILE


def test_platform_changes_nothing():
    assert resolve({}, U, "darwin") == resolve({}, U, "linux")


def test_no_project_folder_segment():
    got = resolve({}, U, "linux")
    segments = {s for path in vars(got).values() for s in path.split("/")}
    assert not segments & {"repos", "projects", "code", "src", "work"}


def test_edge_reads_record_and_writes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("COXSWAIN_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "AGENT_TOOLS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    first = resolve_here(flag=str(tmp_path / "elsewhere"))
    write_home_record(first)
    assert (tmp_path / ".config" / "agent-tools" / "home").read_text() == str(tmp_path / "elsewhere") + "\n"
    assert resolve_here().home == str(tmp_path / "elsewhere")
