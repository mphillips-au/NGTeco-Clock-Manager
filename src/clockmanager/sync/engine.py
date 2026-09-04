"""Pure attendance reconciliation.

This module holds no I/O: given device events and the keys already stored, it
decides what is new. The service layer performs the reads and writes; the GUI
never sees this module directly.

Rules, from ``PHASE-04`` and ``AGENTS.md``:

* Direction comes from ``punch`` alone. ``status`` is raw metadata and is
  preserved verbatim, never interpreted.
* An event is stored even when its ``user_id`` matches no known device user.
  The employee link is resolved later (PHASE 05); dropping the punch would
  destroy evidence.
* Deduplication is by event key first, then by the natural key, so a repeated
  sync inserts nothing even if the stored rows predate event keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clockmanager.domain.models import AttendanceEvent
from clockmanager.sync.keys import build_event_key
from clockmanager.sync.sources import SyncSource

__all__ = [
    "PlannedEvent",
    "ReconcileCounts",
    "ReconcilePlan",
    "plan_inserts",
]


@dataclass(frozen=True, slots=True)
class PlannedEvent:
    """One device event ready to store, with its duplicate key computed."""

    event: AttendanceEvent
    event_key: str
    employee_name: str | None


@dataclass(frozen=True, slots=True)
class ReconcileCounts:
    """What one reconciliation pass observed."""

    seen: int = 0
    new: int = 0
    duplicate: int = 0


@dataclass(frozen=True, slots=True)
class ReconcilePlan:
    """The new rows to insert, plus what was skipped as duplicate."""

    to_insert: tuple[PlannedEvent, ...]
    counts: ReconcileCounts


def plan_inserts(
    *,
    device_id: int,
    events: list[AttendanceEvent],
    known_keys: set[str],
    known_natural_keys: set[tuple[str, datetime, int, int]] | None = None,
    users_by_id: dict[str, str] | None = None,
    source: SyncSource = SyncSource.HISTORICAL,
) -> ReconcilePlan:
    """Decide which device events still need storing.

    :param known_keys: event keys already in the database for this device.
    :param known_natural_keys: ``(user_id, occurred_at, punch, status)`` tuples
        for rows stored before event keys existed. Matched on normalised key
        time so a naive SQLite round trip still counts as the same punch.
    :param users_by_id: ``user_id`` to display-name mapping for the employee
        snapshot. Unknown users get ``None`` rather than being dropped.
    :param source: recorded on the plan for callers that persist it per row.
    """
    from clockmanager.sync.keys import normalise_for_key

    _ = source  # the source is stored by the caller, not by the plan itself.
    natural = known_natural_keys if known_natural_keys is not None else set()
    names = users_by_id if users_by_id is not None else {}
    seen_keys: set[str] = set()
    to_insert: list[PlannedEvent] = []
    duplicates = 0

    for event in events:
        key = build_event_key(
            device_id=device_id,
            user_id=event.user_id,
            occurred_at=event.occurred_at,
            punch=event.punch,
            status=event.status,
        )
        natural_key = (
            event.user_id,
            datetime.strptime(  # noqa: DTZ007 - device-local wall time is naive by construction
                normalise_for_key(event.occurred_at), "%Y-%m-%dT%H:%M:%S"
            ),
            event.punch,
            event.status,
        )
        if key in known_keys or key in seen_keys or natural_key in natural:
            duplicates += 1
            continue
        seen_keys.add(key)
        to_insert.append(
            PlannedEvent(
                event=event,
                event_key=key,
                employee_name=names.get(event.user_id),
            )
        )

    return ReconcilePlan(
        to_insert=tuple(to_insert),
        counts=ReconcileCounts(seen=len(events), new=len(to_insert), duplicate=duplicates),
    )
