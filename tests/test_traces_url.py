import dataclasses

import pytest

from agent_tools.store_url import TracesRoot, profile_traces_root, traces_root

DEFAULT = TracesRoot("/r/traces", False)


def test_unset_or_blank_gives_the_local_traces_dir_in_the_runs_dir():
    assert traces_root(None, "/r") == DEFAULT
    assert traces_root("", "/r") == DEFAULT
    assert profile_traces_root({}, "/r") == DEFAULT


def test_a_local_path_passes_through_as_local():
    assert traces_root("/data/traces", "/r") == TracesRoot("/data/traces", False)


def test_an_s3_url_is_remote_and_kept_verbatim():
    assert traces_root("s3://bucket/prefix", "/r") == TracesRoot("s3://bucket/prefix", True)
    assert traces_root("S3://bucket/p", "/r") == TracesRoot("S3://bucket/p", True)


def test_another_scheme_is_not_object_storage():
    assert traces_root("file:///data/t", "/r") == TracesRoot("file:///data/t", False)


def test_a_relative_path_passes_through_unchanged_like_the_store_url():
    assert traces_root("traces/x", "/r") == TracesRoot("traces/x", False)


def test_the_profile_key_is_traces_url_and_no_other():
    assert profile_traces_root({"traces_url": "s3://b/t"}, "/r") == TracesRoot("s3://b/t", True)
    assert profile_traces_root({"storage_url": "s3://b/t"}, "/r") == DEFAULT


def test_the_root_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT.url = "x"
