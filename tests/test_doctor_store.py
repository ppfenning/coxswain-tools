from agent_tools import doctor
from agent_tools.store_probe import OBJECT_STORE, POSTGRES, ProbeResult

PG_URL = "postgresql://cox_user:hunter2/secret@db.example.com:5432/cox"
TRACES = "s3://cox-traces/runs"
SQLITE_URL = "sqlite:////home/me/.cox/runs/cox.db"
OK_PROBES = [ProbeResult(POSTGRES, True, "reachable"), ProbeResult(OBJECT_STORE, True, "reachable")]


def test_files_mode_is_one_ok_row_about_local_files():
    rows = doctor.store_rows("files", SQLITE_URL, "/home/me/.cox/runs/traces", [])
    assert rows == [
        {"check": "shared store", "ok": True, "detail": "the run store and traces are local files, which is the default"}
    ]
    assert doctor.exit_code(rows) == 0


def test_shared_mode_all_probes_ok_names_host_and_bucket_only():
    rows = doctor.store_rows("shared", PG_URL, TRACES, OK_PROBES)
    assert rows == [
        {
            "check": "shared store",
            "ok": True,
            "detail": "shared store reachable: postgres host db.example.com, traces bucket cox-traces",
        }
    ]
    assert doctor.exit_code(rows) == 0


def test_shared_mode_failing_probe_is_one_fail_row_with_its_detail():
    failing = ProbeResult(POSTGRES, False, "postgres rejected the credentials; check the user, password or keys")
    rows = doctor.store_rows("shared", PG_URL, TRACES, [failing, OK_PROBES[1]])
    assert rows == [{"check": "shared store postgres", "ok": False, "detail": failing.detail}]
    assert doctor.exit_code(rows) == 1


def test_shared_mode_two_failing_probes_give_two_fail_rows():
    probes = [
        ProbeResult(POSTGRES, False, "postgres did not answer in time"),
        ProbeResult(OBJECT_STORE, False, "object_store has no such bucket"),
    ]
    rows = doctor.store_rows("shared", PG_URL, TRACES, probes)
    assert [(r["check"], r["ok"], r["detail"]) for r in rows] == [
        ("shared store postgres", False, "postgres did not answer in time"),
        ("shared store object_store", False, "object_store has no such bucket"),
    ]


def test_shared_mode_with_sqlite_url_is_one_warn_row():
    rows = doctor.store_rows("shared", SQLITE_URL, TRACES, [])
    assert rows == [
        {
            "check": "shared store",
            "ok": True,
            "warn": True,
            "detail": "the setting says shared while the profile still points at local files",
        }
    ]
    assert doctor.exit_code(rows) == 0


def test_shared_mode_with_local_path_is_one_warn_row():
    rows = doctor.store_rows("shared", "/home/me/.cox/runs/cox.db", TRACES, [])
    assert [r.get("warn") for r in rows] == [True]


def test_files_mode_with_postgres_url_is_one_warn_row():
    rows = doctor.store_rows("files", PG_URL, "/home/me/.cox/runs/traces", [])
    assert rows == [
        {
            "check": "shared store",
            "ok": True,
            "warn": True,
            "detail": "the profile points at a shared store while the setting says files",
        }
    ]
    assert doctor.exit_code(rows) == 0


def test_shared_mode_with_local_traces_says_so_instead_of_an_empty_bucket():
    rows = doctor.store_rows("shared", PG_URL, "/home/me/.cox/runs/traces", OK_PROBES)
    assert rows[0]["detail"] == "shared store reachable: postgres host db.example.com, traces are local files"


def test_shared_mode_without_probe_results_is_not_reported_reachable():
    rows = doctor.store_rows("shared", PG_URL, TRACES, [])
    assert [(r["check"], r["ok"], r["detail"]) for r in rows] == [
        ("shared store postgres", False, "not checked"),
        ("shared store object_store", False, "not checked"),
    ]
    assert doctor.exit_code(rows) == 1


def test_shared_mode_with_one_probe_missing_is_not_reported_reachable():
    rows = doctor.store_rows("shared", PG_URL, TRACES, [OK_PROBES[0]])
    assert [(r["check"], r["ok"]) for r in rows] == [("shared store object_store", False)]


def test_connection_string_setting_is_a_fail_row_not_a_local_files_warning():
    rows = doctor.store_rows("shared", "host=db.example.com user=cox password=dsnpass dbname=cox", TRACES, OK_PROBES)
    assert [r["ok"] for r in rows] == [False]
    assert "dsnpass" not in rows[0]["detail"]


def test_unknown_mode_is_one_fail_row():
    rows = doctor.store_rows("cloud", PG_URL, TRACES, [])
    assert [r["ok"] for r in rows] == [False]


def test_slash_in_a_password_or_secret_key_does_not_leak_into_the_host_or_bucket():
    store = "postgresql://cox_user:pa/ss-hunter2@db.example.com/cox"
    traces = "s3://AKIAKEY:wJalr/XYZsecret@cox-traces/runs"
    rows = doctor.store_rows("shared", store, traces, OK_PROBES)
    assert rows[0]["detail"] == "shared store reachable: postgres host db.example.com, traces bucket cox-traces"


def test_no_row_text_holds_a_password_user_or_url():
    traces = "s3://AKIAKEY:wJalr/XYZsecret@cox-traces/runs"
    dsn = "host=db.example.com user=cox_user password=hunter2/secret dbname=cox"
    failing = [ProbeResult(POSTGRES, False, "postgres could not be reached; check the address")]
    cases = [
        ("files", PG_URL, traces, []),
        ("files", SQLITE_URL, traces, []),
        ("shared", PG_URL, traces, OK_PROBES),
        ("shared", PG_URL, traces, failing),
        ("shared", PG_URL, traces, []),
        ("shared", SQLITE_URL, traces, []),
        ("shared", dsn, traces, OK_PROBES),
        ("cloud", PG_URL, traces, OK_PROBES),
    ]
    text = " ".join(f"{r['check']} {r['detail']}" for case in cases for r in doctor.store_rows(*case))
    for secret in ("hunter2", "secret", "cox_user", "AKIAKEY", "wJalr", "XYZsecret", "://", "@"):
        assert secret not in text
