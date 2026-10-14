import pytest

from agent_tools import store_probe
from agent_tools.store_probe import ProbeResult, classify_failure, probe_object_store, probe_postgres, summarize

PASSWORD = "s3cr3t-pw"
ACCESS_KEY = "AKIAEXAMPLEKEY123"
PG_URL = f"postgresql://coxuser:{PASSWORD}@db.example.internal:5432/cox"
BLOCK = {"endpoint": "https://garage.example.internal:3900", "access_key_env": "K", "secret_key_env": "S"}


class InvalidPassword(Exception):
    pass


class NoSuchBucket(Exception):
    pass


class Boom(Exception):
    pass


def _must_not_run(*_args):
    raise AssertionError("connector must not be called")


def _raises(exc):
    def run(*_args):
        raise exc

    return run


def _clean(result: ProbeResult) -> None:
    for secret in (PASSWORD, ACCESS_KEY, "coxuser", "postgresql://", "example.internal"):
        assert secret not in result.detail


def test_postgres_success():
    calls = []
    result = probe_postgres(PG_URL, connect=calls.append)
    assert result.ok and result.component == "postgres"
    assert calls == [PG_URL]
    _clean(result)


def test_postgres_refused_names_component():
    result = probe_postgres(PG_URL, connect=_raises(ConnectionRefusedError(f"refused {PG_URL}")))
    assert not result.ok
    assert "postgres" in result.detail and "refused" in result.detail
    _clean(result)


def test_postgres_auth_error_hides_password():
    result = probe_postgres(PG_URL, connect=_raises(InvalidPassword(f"password {PASSWORD} failed for {PG_URL}")))
    assert not result.ok
    assert "postgres" in result.detail and "credentials" in result.detail
    _clean(result)


@pytest.mark.parametrize("url", ["sqlite:////tmp/cox.db", "/tmp/cox.db"])
def test_postgres_local_files_skip_connector(url):
    assert probe_postgres(url, connect=_must_not_run) == ProbeResult("postgres", True, store_probe.LOCAL_STORE)


S3_URL = "s3://cox-traces/runs"


def test_object_store_success_passes_block_and_bucket():
    calls = []
    result = probe_object_store(BLOCK, S3_URL, head_bucket=lambda block, bucket: calls.append((dict(block), bucket)))
    assert result.ok and result.component == "object_store"
    assert calls == [(BLOCK, "cox-traces")]


def test_object_store_missing_bucket_names_component():
    result = probe_object_store(BLOCK, S3_URL, head_bucket=_raises(NoSuchBucket("no bucket cox-traces")))
    assert not result.ok
    assert "object_store" in result.detail and "bucket" in result.detail
    _clean(result)


def test_object_store_error_hides_key():
    result = probe_object_store(BLOCK, S3_URL, head_bucket=_raises(Boom(f"key {ACCESS_KEY} at {BLOCK['endpoint']}")))
    assert not result.ok
    assert "object_store" in result.detail
    _clean(result)


def test_object_store_local_traces_skip_connector():
    expected = ProbeResult("object_store", True, store_probe.LOCAL_TRACES)
    assert probe_object_store({}, "/var/traces", head_bucket=_must_not_run) == expected
    assert probe_object_store({}, "", head_bucket=_must_not_run) == expected


def test_object_store_url_without_bucket_fails_without_connector():
    result = probe_object_store(BLOCK, "s3://", head_bucket=_must_not_run)
    assert not result.ok and "object_store" in result.detail


def test_summarize_one_line_per_component():
    results = [ProbeResult("postgres", True, "fine"), ProbeResult("object_store", False, "broken")]
    assert summarize(results) == "postgres: ok - fine\nobject_store: failed - broken"


@pytest.mark.parametrize(
    ("name", "word"),
    [
        ("ConnectionRefusedError", "refused"),
        ("TimeoutError", "in time"),
        ("InvalidPassword", "credentials"),
        ("NoSuchBucket", "no such bucket"),
        ("Unheard", "not one this probe recognises"),
    ],
)
def test_classify_failure_sentences(name, word):
    sentence = classify_failure(name, "object_store")
    assert sentence.startswith("object_store ") and word in sentence


def test_classify_failure_real_client_types_per_component():
    pg = classify_failure("OperationalError", "postgres")
    s3 = classify_failure("OSError", "object_store")
    assert pg.startswith("postgres ") and "password" in pg and "network" in pg
    assert s3.startswith("object_store ") and "endpoint" in s3 and "credentials" in s3 and "bucket" in s3
    assert "bucket" not in classify_failure("FileNotFoundError", "postgres")


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (f'connection failed: FATAL:  password authentication failed for user "coxuser" {PASSWORD}', "auth"),
        ("connection failed: Connection refused\n\tIs the server running on that host", "refused"),
        ("connection failed: timeout expired", "timeout"),
        ('could not translate host name "db.example.internal" to address', "unreachable"),
        ("When getting information for bucket 'cox-traces': AWS Error ACCESS_DENIED during HeadBucket", "auth"),
        ("AWS Error NETWORK_CONNECTION during HeadBucket operation: curlCode: 7", "unreachable"),
        ("a postgres store needs psycopg: pip install 'coxswain-tools[postgres]'", "missing_client"),
        ("something else entirely", None),
    ],
)
def test_cause_from_message(message, kind):
    assert store_probe.cause_from_message(message) == kind


def test_malformed_postgres_url_is_a_verdict():
    result = probe_postgres("postgresql://[::1", connect=_must_not_run)
    assert result == ProbeResult("postgres", False, store_probe.BAD_STORE_URL)


class OperationalError(Exception):
    """Named like psycopg's; carries a real-shaped message with the password in it."""


def test_default_postgres_connector_narrows_auth(monkeypatch):
    message = f'connection failed: FATAL:  password authentication failed for user "coxuser" {PG_URL}'
    monkeypatch.setattr(store_probe.store_dialect, "connect_readonly_url", _raises(OperationalError(message)))
    result = probe_postgres(PG_URL)
    assert not result.ok and "credentials" in result.detail
    _clean(result)


def test_default_postgres_connector_unknown_operational_error(monkeypatch):
    monkeypatch.setattr(store_probe.store_dialect, "connect_readonly_url", _raises(OperationalError("odd")))
    result = probe_postgres(PG_URL)
    assert not result.ok and "password" in result.detail and "network" in result.detail


class _FakeS3:
    def __init__(self, error=None, missing=False):
        self.error, self.missing, self.kwargs = error, missing, {}

    def pafs(self):
        fake = self

        class FileType:
            NotFound = "nf"
            Directory = "dir"

        class Info:
            type = FileType.NotFound if fake.missing else FileType.Directory

        class S3FileSystem:
            def __init__(self, **kwargs):
                fake.kwargs.update(kwargs)

            def get_file_info(self, _bucket):
                if fake.error:
                    raise fake.error
                return Info()

        class Namespace:
            pass

        ns = Namespace()
        ns.FileType, ns.S3FileSystem = FileType, S3FileSystem
        ns.AwsStandardS3RetryStrategy = lambda max_attempts: ("standard", max_attempts)
        return ns


def _use_fake_s3(monkeypatch, fake):
    from agent_tools import run_store

    monkeypatch.setattr(run_store, "_import_pyarrow", lambda: (fake.pafs(), None))
    monkeypatch.setenv("K", ACCESS_KEY)
    monkeypatch.setenv("S", PASSWORD)


def test_default_head_bucket_is_bounded_in_time(monkeypatch):
    fake = _FakeS3()
    _use_fake_s3(monkeypatch, fake)
    assert probe_object_store(BLOCK, S3_URL).ok
    assert fake.kwargs["connect_timeout"] == store_probe.S3_TIMEOUT_S
    assert fake.kwargs["request_timeout"] == store_probe.S3_TIMEOUT_S
    assert fake.kwargs["retry_strategy"] == ("standard", store_probe.S3_MAX_ATTEMPTS)


def test_default_head_bucket_missing_bucket(monkeypatch):
    _use_fake_s3(monkeypatch, _FakeS3(missing=True))
    result = probe_object_store(BLOCK, S3_URL)
    assert not result.ok and "no such bucket" in result.detail


def test_default_head_bucket_access_denied_hides_key(monkeypatch):
    error = OSError(f"AWS Error ACCESS_DENIED during HeadBucket operation: key {ACCESS_KEY}")
    _use_fake_s3(monkeypatch, _FakeS3(error=error))
    result = probe_object_store(BLOCK, S3_URL)
    assert not result.ok and "credentials" in result.detail
    _clean(result)


def test_default_head_bucket_unset_env_is_misconfigured(monkeypatch):
    _use_fake_s3(monkeypatch, _FakeS3())
    monkeypatch.delenv("K")
    result = probe_object_store(BLOCK, S3_URL)
    assert not result.ok and "settings are incomplete" in result.detail
