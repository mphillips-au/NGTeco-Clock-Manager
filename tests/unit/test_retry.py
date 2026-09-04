"""Retry and reconnect policy tests."""

from __future__ import annotations

import pytest

from clockmanager.protocol.errors import (
    DeviceConnectionError,
    DeviceParseError,
    DeviceTimeoutError,
)
from clockmanager.protocol.retry import RetryPolicy, call_with_retry


def _no_sleep(_seconds: float) -> None:
    return None


def test_succeeds_first_time() -> None:
    calls: list[int] = []

    def work() -> str:
        calls.append(1)
        return "ok"

    assert call_with_retry(work, policy=RetryPolicy(), description="test", sleep=_no_sleep) == "ok"
    assert len(calls) == 1


def test_retries_until_success() -> None:
    attempts: list[int] = []

    def work() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise DeviceConnectionError("dropped")
        return "ok"

    result = call_with_retry(
        work, policy=RetryPolicy(attempts=3), description="test", sleep=_no_sleep
    )
    assert result == "ok"
    assert len(attempts) == 3


def test_gives_up_after_the_configured_attempts() -> None:
    attempts: list[int] = []

    def work() -> str:
        attempts.append(1)
        raise DeviceConnectionError("dropped")

    with pytest.raises(DeviceConnectionError, match="after 2 attempt"):
        call_with_retry(work, policy=RetryPolicy(attempts=2), description="test", sleep=_no_sleep)
    assert len(attempts) == 2


def test_error_subtype_survives_exhaustion() -> None:
    """A caller must still be able to tell a timeout from a dropped link."""

    def work() -> str:
        raise DeviceTimeoutError("too slow")

    with pytest.raises(DeviceTimeoutError):
        call_with_retry(work, policy=RetryPolicy(attempts=2), description="test", sleep=_no_sleep)


def test_non_connection_errors_are_not_retried() -> None:
    """A bad parse would just repeat; only connection faults are transient."""
    attempts: list[int] = []

    def work() -> str:
        attempts.append(1)
        raise DeviceParseError("unrecognised layout")

    with pytest.raises(DeviceParseError):
        call_with_retry(work, policy=RetryPolicy(attempts=3), description="test", sleep=_no_sleep)
    assert len(attempts) == 1


def test_reconnect_hook_runs_before_each_retry_only() -> None:
    reconnects: list[int] = []
    attempts: list[int] = []

    def work() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise DeviceConnectionError("dropped")
        return "ok"

    call_with_retry(
        work,
        policy=RetryPolicy(attempts=3),
        description="test",
        on_retry=lambda: reconnects.append(1),
        sleep=_no_sleep,
    )
    assert len(attempts) == 3
    assert len(reconnects) == 2


def test_a_failing_reconnect_does_not_abort_remaining_attempts() -> None:
    reconnects: list[int] = []

    def failing_reconnect() -> None:
        reconnects.append(1)
        raise DeviceConnectionError("still down")

    def work() -> str:
        raise DeviceConnectionError("dropped")

    with pytest.raises(DeviceConnectionError):
        call_with_retry(
            work,
            policy=RetryPolicy(attempts=3),
            description="test",
            on_retry=failing_reconnect,
            sleep=_no_sleep,
        )
    assert len(reconnects) == 2


def test_backoff_grows_and_is_capped() -> None:
    policy = RetryPolicy(
        attempts=6, initial_backoff_seconds=1.0, backoff_multiplier=2.0, max_backoff_seconds=4.0
    )
    assert [policy.backoff_for(attempt) for attempt in range(1, 7)] == [
        0.0,
        1.0,
        2.0,
        4.0,
        4.0,
        4.0,
    ]


def test_sleeps_between_attempts() -> None:
    slept: list[float] = []

    def work() -> str:
        raise DeviceConnectionError("dropped")

    with pytest.raises(DeviceConnectionError):
        call_with_retry(
            work,
            policy=RetryPolicy(attempts=3, initial_backoff_seconds=0.5),
            description="test",
            sleep=slept.append,
        )
    assert slept == [0.5, 1.0]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attempts": 0},
        {"initial_backoff_seconds": -1},
        {"backoff_multiplier": 0.5},
    ],
)
def test_invalid_policies_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]
