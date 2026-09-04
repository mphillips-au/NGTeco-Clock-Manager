"""Where a stored attendance punch came from.

``historical`` covers the initial full sync and every later incremental
reconciliation: both read the device's stored log, so they are the same source.
``manual`` is an operator pressing "Sync now". ``live`` is a punch arriving
through live capture. ``background`` is the periodic automatic sync.
``recovery`` is the first successful sync after the device was unreachable,
which re-reads the whole log to pick up anything missed while offline.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["SyncSource"]


class SyncSource(StrEnum):
    """Provenance of one stored attendance row or one sync run."""

    HISTORICAL = "historical"
    MANUAL = "manual"
    LIVE = "live"
    BACKGROUND = "background"
    RECOVERY = "recovery"
