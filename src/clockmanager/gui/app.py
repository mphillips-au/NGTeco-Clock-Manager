"""Qt application entry point."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from clockmanager import APPLICATION_NAME, __version__
from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.gui.main_window import MainWindow
from clockmanager.services.application import ApplicationContext

__all__ = ["build_application", "run_gui"]

_logger = get_logger(__name__)


def build_application(argv: list[str] | None = None) -> QApplication:
    """Return the process-wide ``QApplication``, creating it if needed."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing

    app = QApplication(argv if argv is not None else [])
    app.setApplicationName(APPLICATION_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APPLICATION_NAME)
    return app


def run_gui(context: ApplicationContext, argv: list[str] | None = None) -> int:
    """Show the main window and run the Qt event loop until it closes."""
    app = build_application(argv)
    window = MainWindow(context)
    window.show()
    _logger.info("GUI started")
    return int(app.exec())
