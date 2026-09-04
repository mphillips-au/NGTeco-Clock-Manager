"""Synchronisation layer (PHASE 04).

Reliable live + historical attendance sync on top of the natural key declared
by :class:`clockmanager.persistence.models.AttendanceEventRecord`:

* :mod:`clockmanager.sync.keys` — deterministic duplicate-detection keys.
* :mod:`clockmanager.sync.engine` — pure reconciliation (what is new?).
* :mod:`clockmanager.sync.sources` — where a stored punch came from.

The layer is pure Python: no PySide6, no SQLAlchemy, no sockets. Reads and
writes live in :mod:`clockmanager.services.sync`, which owns the device
connection and the database transaction.
"""

from __future__ import annotations

from clockmanager.sync.engine import PlannedEvent, ReconcileCounts, ReconcilePlan, plan_inserts
from clockmanager.sync.keys import build_event_key, normalise_for_key
from clockmanager.sync.sources import SyncSource

__all__ = [
    "PlannedEvent",
    "ReconcileCounts",
    "ReconcilePlan",
    "SyncSource",
    "build_event_key",
    "normalise_for_key",
    "plan_inserts",
]
