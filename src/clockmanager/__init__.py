"""NGTeco Clock Manager.

Windows-first attendance management application for NGTeco NG-MB1 devices.

Layering (see ``ARCHITECTURE.md``)::

    gui  ->  services  ->  domain / persistence / protocol / sync

Only :mod:`clockmanager.gui` may import PySide6. Every other subpackage must
stay usable from a headless Linux/Synology service.
"""

from __future__ import annotations

__all__ = ["APPLICATION_NAME", "__version__"]

APPLICATION_NAME = "NGTeco Clock Manager"
__version__ = "0.14.0"
