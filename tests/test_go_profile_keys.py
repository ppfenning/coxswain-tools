"""go/testdata/profile-keys.json is route._KNOWN_KEYS; go/usage_test.go checks the Go list against the same file.
`pytest --regen` rewrites it from Python before comparing."""

import json
from pathlib import Path

from agent_tools import route

KEYS = Path(__file__).resolve().parent.parent / "go" / "testdata" / "profile-keys.json"


def test_go_profile_keys_file_is_python_known_keys(request):
    expected = json.dumps(sorted(route._KNOWN_KEYS), indent=2) + "\n"
    if request.config.getoption("--regen"):
        KEYS.write_text(expected, encoding="utf-8")
    assert KEYS.read_text(encoding="utf-8") == expected
