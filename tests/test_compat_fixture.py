"""Shape contract for compat_manifest_fixture.json.

{"components": [{"name", "version", "requires": {<component>: {"min", "max"}}}], "feed_schema_version": int}
Versions are bare MAJOR.MINOR.PATCH; min and max are inclusive.
"""

import json
import re
from pathlib import Path

FIXTURE = Path(__file__).parent / "compat_manifest_fixture.json"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_exact_key_set():
    data = load_fixture()
    assert set(data) == {"components", "feed_schema_version"}
    for component in data["components"]:
        assert set(component) == {"name", "version", "requires"}
        for bounds in component["requires"].values():
            assert set(bounds) == {"min", "max"}


def test_versions_are_bare_semver():
    data = load_fixture()
    versions = [
        v
        for c in data["components"]
        for v in [c["version"], *(x for b in c["requires"].values() for x in (b["min"], b["max"]))]
    ]
    assert versions
    assert [v for v in versions if not SEMVER.match(v)] == []
    feed = data["feed_schema_version"]
    assert isinstance(feed, int) and not isinstance(feed, bool)


def test_requires_name_listed_components():
    components = load_fixture()["components"]
    names = {c["name"] for c in components}
    assert len(names) == len(components)
    for c in components:
        assert set(c["requires"]) <= names
        assert c["name"] not in c["requires"]
