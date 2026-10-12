"""Classify a failed CI run whose jobs never got past setup."""

from collections.abc import Mapping

_PASSING = ("success", "skipped", "neutral")


def _is_failed(job: Mapping) -> bool:
    return job.get("conclusion") not in _PASSING


def _never_started(job: Mapping) -> bool:
    return not any(step.get("name") != "Set up job" and bool(step.get("conclusion")) for step in job.get("steps") or [])


def never_started(payload: Mapping) -> bool:
    """True when jobs failed and none ran a step past "Set up job"; a later step conclusion is a real failure."""
    failed = [job for job in payload.get("jobs") or [] if _is_failed(job)]
    return bool(failed) and all(_never_started(job) for job in failed)
