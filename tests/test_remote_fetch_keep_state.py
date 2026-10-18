from agent_tools.remote_fetch import changed_after_start


def test_a_row_updated_after_the_run_started_is_kept():
    assert changed_after_start("2026-10-17T20:38:12Z", "2026-10-17T20:00:00+00:00") is True


def test_a_row_updated_before_the_run_started_takes_the_runs_outcome():
    assert changed_after_start("2026-10-17T19:59:59Z", "2026-10-17T20:00:00+00:00") is False
