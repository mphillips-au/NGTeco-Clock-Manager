"""Linux/Synology headless service (PHASE 16).

The same device/sync core the Windows GUI uses, without PySide6: periodic
reconciliation of every enabled device through :class:`SyncService`, an
optional live-capture worker per device, a stdlib-only health endpoint for
container orchestration, and signal-driven graceful shutdown.

Nothing here duplicates protocol code. This package calls application
services only (``ARCHITECTURE.md``): device reads go through
``context.devices`` / ``context.sync``, and the 120-byte parser, attendance
engine, live capture and reconciliation all run inside those layers.
"""

from __future__ import annotations

from clockmanager.headless.health import HealthServer, parse_health_bind
from clockmanager.headless.runner import HeadlessService, ServiceSnapshot

__all__ = ["HeadlessService", "HealthServer", "ServiceSnapshot", "parse_health_bind"]
