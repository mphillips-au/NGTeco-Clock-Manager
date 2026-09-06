"""Web/API boundary over the headless core (PHASE 17).

The browser talks to this package over HTTPS/REST only. It never touches
TCP 4370 directly: every route goes through the application services in
:mod:`clockmanager.services`, which own the MB1 protocol adapter. No
protocol logic is duplicated here — this layer only translates between
HTTP and the existing service calls.

This subpackage must stay free of PySide6, exactly like the rest of the
reusable core (``ARCHITECTURE.md``).
"""

from __future__ import annotations

from clockmanager.api.app import create_app

__all__ = ["create_app"]
