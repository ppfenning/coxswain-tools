from tests.startup_timing import CHAIR_STATUS_TARGET_S, HELP_TARGET_S, check_ceiling


def test_passes_under_the_multiple():
    assert check_ceiling(0.05, 0.02, 4.0, HELP_TARGET_S).ok is True


def test_fails_over_the_multiple():
    assert check_ceiling(0.15, 0.02, 4.0, HELP_TARGET_S).ok is False


def test_failure_message_names_measurements_and_absolute_target():
    message = check_ceiling(0.15, 0.02, 4.0, CHAIR_STATUS_TARGET_S).message
    assert message == (
        "cox took 0.150s against a 0.020s interpreter baseline: "
        "ratio 7.50x exceeds the 4.00x multiple (documented local target 0.30s)"
    )
