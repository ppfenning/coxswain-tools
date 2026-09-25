import json

from test_doctor_cli import _good_setup

from agent_tools import doctor, run_store
from agent_tools.cli import main


def _profile(tmp_path):
    profile = tmp_path / "profile.yaml"
    profile.write_text("team: acme\nworkspace_dir: " + str(tmp_path / "ws") + "\n")
    return profile


def _patch(monkeypatch, readable, reason, seen=None):
    def fake(root):
        if seen is not None:
            seen.append(root)
        return run_store.ParquetCheck(readable, reason)

    monkeypatch.setattr(run_store, "parquet_readable", fake)


def test_readable_line():
    assert doctor.parquet_line(True, "ok") == "parquet traces: readable"


def test_pyarrow_missing_line_names_reason_and_the_parquet_extra():
    assert doctor.parquet_line(False, "pyarrow missing") == (
        "parquet traces: not readable (pyarrow missing); fix: install the parquet extra"
    )


def test_root_unreachable_line_names_the_traces_root_not_the_parquet_extra():
    assert doctor.parquet_line(False, "root unreachable") == (
        "parquet traces: not readable (root unreachable); "
        "fix: create the traces directory, or correct traces_url in the provider profile"
    )


def test_an_unknown_reason_is_shown_with_no_fix():
    assert doctor.parquet_line(False, "something new") == "parquet traces: not readable (something new)"


def test_doctor_prints_the_readable_line_for_the_default_traces_root(tmp_path, monkeypatch, capsys):
    seen = []
    _patch(monkeypatch, True, "ok", seen)
    main(["setup", "doctor", "--profile", str(_profile(tmp_path))])
    assert capsys.readouterr().out.splitlines()[-1] == "parquet traces: readable"
    assert [(r.url, r.remote) for r in seen] == [(str(tmp_path / "ws" / "runs" / "traces"), False)]


def test_doctor_prints_the_root_unreachable_line(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch, False, "root unreachable")
    main(["setup", "doctor", "--profile", str(_profile(tmp_path))])
    assert capsys.readouterr().out.splitlines()[-1] == doctor.parquet_line(False, "root unreachable")


def test_a_missing_pyarrow_keeps_a_passing_doctor_passing(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    _patch(monkeypatch, True, "ok")
    rc_ok = main(["setup", "doctor", "--profile", str(profile), "--json"])
    doc_ok = json.loads(capsys.readouterr().out)
    _patch(monkeypatch, False, "pyarrow missing")
    rc_missing = main(["setup", "doctor", "--profile", str(profile), "--json"])
    doc_missing = json.loads(capsys.readouterr().out)
    assert (rc_ok, rc_missing) == (0, 0)
    assert (doc_ok["ok"], doc_missing["ok"]) == (True, True)
    assert doc_missing["rows"] == doc_ok["rows"]
    assert doc_missing["parquet_traces"] == (
        "parquet traces: not readable (pyarrow missing); fix: install the parquet extra"
    )


def test_no_line_and_a_null_json_key_when_the_profile_names_no_workspace(tmp_path, monkeypatch, capsys):
    seen = []
    _patch(monkeypatch, False, "pyarrow missing", seen)
    absent = str(tmp_path / "absent.yaml")
    main(["setup", "doctor", "--profile", absent])
    assert "parquet traces" not in capsys.readouterr().out
    main(["setup", "doctor", "--profile", absent, "--json"])
    assert json.loads(capsys.readouterr().out)["parquet_traces"] is None
    assert seen == []
