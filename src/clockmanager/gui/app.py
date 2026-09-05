"""Qt application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog

from clockmanager import APPLICATION_NAME, __version__
from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.gui.main_window import MainWindow
from clockmanager.gui.views.auth import BootstrapAdminDialog, LoginDialog
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser

__all__ = ["build_application", "run_gui"]

_logger = get_logger(__name__)


def _find_app_icon() -> QIcon | None:
    """Locate the bundled or packaged application icon."""
    meipass = getattr(sys, "_MEIPASS", None)
    candidates = [
        Path(sys.executable).parent / "clockmanager.ico",
        Path(sys.executable).parent / "assets" / "clockmanager.ico",
        Path(__file__).resolve().parents[3] / "packaging" / "assets" / "clockmanager.ico",
    ]
    if meipass:
        candidates.insert(0, Path(meipass) / "clockmanager.ico")
        candidates.insert(1, Path(meipass) / "assets" / "clockmanager.ico")

    for c in candidates:
        if c.is_file():
            return QIcon(str(c))
    return None


def build_application(argv: list[str] | None = None) -> QApplication:
    """Return the process-wide ``QApplication``, creating it if needed."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing

    app = QApplication(argv if argv is not None else [])
    app.setApplicationName(APPLICATION_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APPLICATION_NAME)

    icon = _find_app_icon()
    if icon is not None and not icon.isNull():
        app.setWindowIcon(icon)

    return app


def _ensure_login(context: ApplicationContext) -> AuthenticatedUser | None:
    """Run first-run setup or the login dialog. ``None`` means the operator
    cancelled and the application should exit without opening anything."""
    auth = context.auth
    if auth.needs_setup():
        setup = BootstrapAdminDialog(auth)
        if setup.exec() != QDialog.DialogCode.Accepted or setup.user is None:
            return None
        return setup.user
    login = LoginDialog(auth)
    if login.exec() != QDialog.DialogCode.Accepted or login.user is None:
        return None
    return login.user


def run_gui(context: ApplicationContext, argv: list[str] | None = None) -> int:
    """Log in, show the main window and run the Qt event loop.

    Choosing Logout closes the window and returns to the login dialog, so
    one process can serve consecutive operators with an audit trail for
    each session.
    """
    app = build_application(argv)
    while True:
        user = _ensure_login(context)
        if user is None:
            return 0
        window = MainWindow(context, current_user=user)
        window.show()
        _logger.info("GUI started", extra={"actor": user.username, "role": user.role.value})
        app.exec()
        if not window.logout_requested:
            return 0
        context.auth.logout()
