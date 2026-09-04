"""Deterministic attendance event keys.

Duplicate prevention (``TESTING.md``) rests on a stable identity for one punch:
the same device history read twice must produce the same key, so the second
read inserts nothing.

The key covers the device natural key declared by
:class:`clockmanager.persistence.models.AttendanceEventRecord`
(``device_id``, ``user_id``, ``occurred_at``, ``punch``, ``status``) and is a
SHA-256 hex digest, so it fits in a short indexed column and never carries a
user identifier in the clear beyond what the row already holds.

Timezone handling is deliberate (``STATUS.md``): the device transmits naive
device-local time with second precision. SQLite returns naive datetimes on
read. To keep the key stable across that round trip, an aware datetime is
first normalised to UTC and then compared by its wall-clock seconds; naive
values are used as-is. Microseconds are dropped because no MB1 timestamp
carries them.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

__all__ = ["build_event_key", "normalise_for_key"]


def normalise_for_key(moment: datetime) -> str:
    """Return the canonical ``YYYY-MM-DDTHH:MM:SS`` string used inside keys."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def build_event_key(
    *,
    device_id: int,
    user_id: str,
    occurred_at: datetime,
    punch: int,
    status: int,
) -> str:
    """Build the stable duplicate-detection key for one attendance punch."""
    canonical = "|".join(
        (
            str(device_id),
            user_id,
            normalise_for_key(occurred_at),
            str(punch),
            str(status),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
