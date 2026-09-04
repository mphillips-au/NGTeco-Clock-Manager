"""Synchronisation layer.

PHASE 00 defines the boundary only. Historical attendance sync, live capture
handling, duplicate detection and reconciliation are built in PHASE 04 on top
of the natural key declared by
:class:`clockmanager.persistence.models.AttendanceEventRecord`.
"""

from __future__ import annotations

__all__: list[str] = []
