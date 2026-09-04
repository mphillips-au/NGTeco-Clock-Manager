"""Retry and reconnect policy.

Device links drop. A read that fails because the socket died should be retried
once the link is re-established, but a read that fails because the payload was
unparseable must not be — retrying would just produce the same bad parse.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.protocol.errors import DeviceConnectionError

__all__ = ["RetryPolicy", "call_with_retry"]

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How often and how quickly to retry a failed device call."""

    attempts: int = 3
    initial_backoff_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must not be negative")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be at least 1")

    def backoff_for(self, attempt: int) -> float:
        """Seconds to wait before ``attempt`` (1-based)."""
        if attempt <= 1:
            return 0.0
        delay = self.initial_backoff_seconds * (self.backoff_multiplier ** (attempt - 2))
        return min(delay, self.max_backoff_seconds)


def call_with_retry[T](
    operation: Callable[[], T],
    *,
    policy: RetryPolicy,
    description: str,
    on_retry: Callable[[], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``operation``, retrying only on connection failures.

    ``on_retry`` is the reconnect hook; it runs before each retry. Failures
    inside it are folded into the retry loop so a device that is still down
    does not abort the remaining attempts.

    Parse and capability errors propagate on the first failure by design.
    """
    last_error: DeviceConnectionError | None = None

    for attempt in range(1, policy.attempts + 1):
        delay = policy.backoff_for(attempt)
        if delay:
            sleep(delay)

        if attempt > 1 and on_retry is not None:
            try:
                on_retry()
            except DeviceConnectionError as exc:
                last_error = exc
                _logger.warning(
                    "Reconnect before retry failed",
                    extra={"operation": description, "attempt": attempt},
                )
                continue

        try:
            return operation()
        except DeviceConnectionError as exc:
            last_error = exc
            _logger.warning(
                "Device operation failed",
                extra={
                    "operation": description,
                    "attempt": attempt,
                    "attempts_allowed": policy.attempts,
                    "error": str(exc),
                },
            )

    assert last_error is not None

    # Re-raise as the same concrete type so callers can still distinguish a
    # timeout from a dropped link after the retries are exhausted.
    message = f"{description} failed after {policy.attempts} attempt(s): {last_error}"
    try:
        raise type(last_error)(message) from last_error
    except TypeError:  # pragma: no cover - a subclass with a different signature
        raise DeviceConnectionError(message) from last_error
