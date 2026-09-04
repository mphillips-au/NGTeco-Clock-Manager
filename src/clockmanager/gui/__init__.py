"""PySide6 GUI layer.

This is the only subpackage permitted to import PySide6. It talks to
:mod:`clockmanager.services` and never to the protocol or persistence layers
directly.

Importing this package requires PySide6 to be installed
(``pip install -e .[gui]``).
"""

from __future__ import annotations

from clockmanager.gui.app import run_gui

__all__ = ["run_gui"]
