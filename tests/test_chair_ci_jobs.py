from agent_tools.chair_ci_jobs import never_started


def test_only_setup_step_with_null_conclusion_is_never_started():
    payload = {
        "jobs": [
            {
                "name": "test",
                "conclusion": None,
                "steps": [{"name": "Set up job", "number": 1, "conclusion": None}],
            }
        ]
    }
    assert never_started(payload) is True


def test_failed_job_without_steps_is_never_started():
    payload = {"jobs": [{"name": "test", "conclusion": "failure", "steps": []}]}
    assert never_started(payload) is True


def test_later_failed_step_is_a_real_failure():
    payload = {
        "jobs": [
            {
                "name": "test",
                "conclusion": "failure",
                "steps": [
                    {"name": "Set up job", "number": 1, "conclusion": "success"},
                    {"name": "Run tests", "number": 2, "conclusion": "failure"},
                ],
            }
        ]
    }
    assert never_started(payload) is False


def test_one_real_failure_among_never_started_jobs_is_false():
    payload = {
        "jobs": [
            {
                "name": "lint",
                "conclusion": "failure",
                "steps": [{"name": "Set up job", "number": 1, "conclusion": ""}],
            },
            {
                "name": "test",
                "conclusion": "failure",
                "steps": [
                    {"name": "Set up job", "number": 1, "conclusion": "success"},
                    {"name": "Run tests", "number": 2, "conclusion": "failure"},
                ],
            },
        ]
    }
    assert never_started(payload) is False


def test_all_jobs_success_has_no_failed_job():
    payload = {
        "jobs": [
            {
                "name": "test",
                "conclusion": "success",
                "steps": [{"name": "Set up job", "number": 1, "conclusion": "success"}],
            }
        ]
    }
    assert never_started(payload) is False


def test_empty_jobs_is_false():
    assert never_started({"jobs": []}) is False
