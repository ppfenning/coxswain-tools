import subprocess

from agent_tools import cartridge_screen, editor_model
from agent_tools.editor_model import Effect, State
from agent_tools.fragments import FragmentError, load_fragment


def _fake_run(returncode=0, stdout="", stderr=""):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return run, calls


def test_write_fragment_calls_write_and_turns_a_refusal_into_a_provenance_error(tmp_path):
    seen = []
    ok = cartridge_screen.run_effect(
        Effect("write_fragment", {"edits": {"a": 1}}),
        {"cartridges_dir": str(tmp_path), "team": "acme"},
        run=None, write=lambda d, e: seen.append((d, e)),
    )
    assert ok == {}
    assert seen == [(tmp_path / "acme", {"a": 1})]

    def refuse(team_dir, edits):
        raise FragmentError("edited.yaml was hand-edited")

    refused = cartridge_screen.run_effect(
        Effect("write_fragment", {"edits": {}}), {"cartridges_dir": "x", "team": "y"}, run=None, write=refuse
    )
    assert refused == {"provenance_error": "edited.yaml was hand-edited"}


def test_run_probe_argv_json_success_and_the_two_refusal_shapes():
    run, calls = _fake_run(stdout='{"resolved": {}}')
    ctx = {"probe_argv": ["cartridge", "probe", "--team", "acme"]}
    assert cartridge_screen.run_effect(Effect("run_probe", {}), ctx, run=run, write=None) == {"resolved": {}}
    assert calls == [["cartridge", "probe", "--team", "acme"]]

    run_failing, _ = _fake_run(returncode=1, stderr="boom")
    refused = cartridge_screen.run_effect(Effect("run_probe", {}), {"probe_argv": ["x"]}, run=run_failing, write=None)
    assert refused == {"provenance_error": "boom"}

    run_garbled, _ = _fake_run(stdout="not json")
    garbled = cartridge_screen.run_effect(Effect("run_probe", {}), {"probe_argv": ["x"]}, run=run_garbled, write=None)
    assert "provenance_error" in garbled


def _raising_run(argv, **kwargs):
    raise FileNotFoundError(2, "No such file or directory", argv[0])


def test_a_binary_that_cannot_launch_is_a_value_for_both_subprocess_kinds():
    probe = cartridge_screen.run_effect(Effect("run_probe", {}), {"probe_argv": ["nope"]}, run=_raising_run, write=None)
    assert "provenance_error" in probe and "nope" in probe["provenance_error"]
    init = cartridge_screen.run_effect(Effect("init_cartridge", {"name": "acme", "extends": "base"}),
                                       {"cartridges_dir": "/carts"}, run=_raising_run, write=None)
    assert init["returncode"] == 127 and init["team"] == "acme" and "cartridge" in init["output"]


def test_init_cartridge_argv_shape_and_tailed_output():
    run, calls = _fake_run(stdout="\n".join(f"line{i}" for i in range(8)))
    effect = Effect("init_cartridge", {"name": "acme", "extends": "base"})
    result = cartridge_screen.run_effect(effect, {"cartridges_dir": "/carts"}, run=run, write=None)
    assert calls == [["cartridge", "init", "acme", "--cartridges-dir", "/carts", "--extends", "base"]]
    assert result == {"returncode": 0, "team": "acme", "output": "\n".join(f"line{i}" for i in range(3, 8))}


def test_set_profile_team_replaces_only_the_team_line_and_appends_when_absent(tmp_path):
    profile = tmp_path / "profile.yaml"
    profile.write_text("team: old\ncartridges_dir: /carts\nassume: a\n")
    effect = Effect("set_profile_team", {"team": "new"})
    result = cartridge_screen.run_effect(effect, {"profile_path": str(profile)}, run=None, write=None)
    assert result == {"returncode": 0, "team": "new"}
    assert profile.read_text() == "team: new\ncartridges_dir: /carts\nassume: a\n"

    profile.write_text("cartridges_dir: /carts\n")
    cartridge_screen.run_effect(effect, {"profile_path": str(profile)}, run=None, write=None)
    assert profile.read_text() == "cartridges_dir: /carts\nteam: new\n"


def test_profile_fields_expands_a_leading_tilde_against_the_home_argument():
    text = "team: acme\ncartridges_dir: ~/carts\n"
    # "assume" is route.parse_profile's own default when the text is silent on it.
    assert cartridge_screen.profile_fields(text, home="/home/pat") == {
        "team": "acme", "cartridges_dir": "/home/pat/carts", "assume": "a",
    }


def test_a_real_write_fragment_effect_from_editor_model_runs_against_the_real_write_fragment(tmp_path):
    # editor_model._write is the only builder of a write_fragment Effect; drive
    # it through step and run with the real write_fragment default, no fake,
    # so both the payload key and write_fragment's signature are the real ones.
    row = editor_model.Row(key="policy.review_tier", value="1", layer="acme",
                            editable=True, kind="choice", choices=("1", "2", "3"))
    fresh = editor_model.State(rows=(row,), cursor=0, pending={}, message="", team="acme")
    toggled = editor_model.step(fresh, "space")
    written = editor_model.step(toggled, "w")
    effect = written.effects[0]
    assert effect.kind == "write_fragment"

    result = cartridge_screen.run_effect(effect, {"cartridges_dir": str(tmp_path), "team": "acme"})

    assert result == {}
    written = (tmp_path / "acme" / "cartridge.d" / "edited.yaml").read_text()
    assert load_fragment(written) == {"policy": {"review_tier": "2"}}


def test_a_real_run_probe_effect_from_editor_model_has_no_payload_keys_to_assume():
    # editor_model._refresh builds Effect("run_probe", {}); _run_probe never
    # reads effect.payload, so the real, empty payload exercises it end to end.
    fresh = editor_model.State(rows=(), cursor=0, pending={}, message="", team="acme")
    refreshed = editor_model.step(fresh, "r")
    effect = refreshed.effects[0]
    assert effect == Effect("run_probe", {})
    run, calls = _fake_run(stdout="{}")
    assert cartridge_screen.run_effect(effect, {"probe_argv": ["x"]}, run=run, write=None) == {}
    assert calls == [["x"]]


class _FakeStdscr:
    def __init__(self, keys, size=(24, 80)):
        self._keys = list(keys)
        self._size = size
        self.addnstr_calls = []

    def getmaxyx(self):
        return self._size

    def clear(self):
        pass

    def refresh(self):
        pass

    def addnstr(self, y, x, s, n):
        self.addnstr_calls.append((y, x, s, n))

    def getch(self):
        return self._keys.pop(0)


_EMPTY_STATE = State(rows=(), cursor=0, pending={}, message="")


def test_should_quit_is_a_bare_q_outside_of_editing_only():
    assert cartridge_screen._should_quit(_EMPTY_STATE, "q")
    assert not cartridge_screen._should_quit(_EMPTY_STATE, "j")
    editing = State(rows=(), cursor=0, pending={}, message="", editing="k")
    assert not cartridge_screen._should_quit(editing, "q")


def test_q_ends_the_loop_without_calling_the_runner():
    runs = []
    stdscr = _FakeStdscr([ord("q")])
    cartridge_screen.loop(stdscr, _EMPTY_STATE, {}, runner=lambda effect, ctx: runs.append(effect))
    assert runs == []


def test_loop_runs_one_effect_through_the_runner_and_folds_the_result(monkeypatch):
    effect = Effect("write_fragment", {"edits": {"a": 1}})
    with_effect = State(rows=(), cursor=0, pending={}, message="", effects=(effect,))
    monkeypatch.setattr(cartridge_screen, "step", lambda state, key: with_effect)

    runner_calls = []
    stdscr = _FakeStdscr([ord("x"), ord("q")])
    cartridge_screen.loop(stdscr, _EMPTY_STATE, {},
                           runner=lambda eff, ctx: runner_calls.append(eff) or {"provenance_error": "boom"})

    assert runner_calls == [effect]
    assert any("boom" in call[2] for call in stdscr.addnstr_calls)


def test_e_then_typed_characters_then_enter_reaches_apply_text_through_step():
    # Closes finding 1: the loop makes no text-entry decision; `step` does.
    row = editor_model.Row(key="cartridges_dir", value="", layer="acme", editable=True, kind="text")
    state = State(rows=(row,), cursor=0, pending={}, message="", team="acme")
    for key in ("e", "a", "b", "ENTER"):
        state = editor_model.step(state, key)
    assert state.pending == {"cartridges_dir": "ab"}


def test_main_wires_the_real_probe_keys_straight_into_editor_model_rows(monkeypatch, tmp_path):
    import curses

    facts = {"resolved": {"policy": {"review_tier": "2"}}, "provenance": {"policy.review_tier": "acme"}}
    seen = {}
    monkeypatch.setattr(cartridge_screen, "loop", lambda stdscr, state, ctx, **kw: seen.update(state=state))
    monkeypatch.setattr(curses, "wrapper", lambda fn: fn(None))

    rc = cartridge_screen.main({"root": str(tmp_path), "team": "acme", "workspace": str(tmp_path / "work")},
                                probe=lambda *a, **kw: facts)

    assert rc == 0
    assert [r.key for r in seen["state"].rows] == ["policy.review_tier"]
    assert seen["state"].rows[0].layer == "acme"


def test_a_venv_path_the_process_may_not_read_is_simply_absent(monkeypatch):
    def denied(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(cartridge_screen.Path, "exists", denied)
    assert cartridge_screen._present("/root/agent-cartridges/.venv/bin/cartridge") is False
