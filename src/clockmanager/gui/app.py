"""Qt application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from clockmanager import APPLICATION_NAME, __version__
from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.gui.main_window import MainWindow
from clockmanager.gui.theme import current_theme, set_theme
from clockmanager.gui.views.auth import BootstrapAdminDialog, LoginDialog
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser

__all__ = ["FocusVisibilityFilter", "build_application", "run_gui"]

#: The ways focus arrives that mean somebody is navigating by keyboard.
_KEYBOARD_REASONS = frozenset(
    {
        Qt.FocusReason.TabFocusReason,
        Qt.FocusReason.BacktabFocusReason,
        Qt.FocusReason.ShortcutFocusReason,
    }
)


class FocusVisibilityFilter(QObject):
    """Marks widgets that received focus from the keyboard.

    A focus ring is essential for anyone driving the application by keyboard
    and looks like a defect when it appears around a button somebody simply
    clicked. Qt has no ``:focus-visible``, so the focus reason is recorded on
    the widget as ``focusVisible`` and the stylesheet keys the ring off that.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if isinstance(watched, QWidget):
            if event.type() == QEvent.Type.FocusIn:
                reason = event.reason()  # type: ignore[attr-defined]
                self._mark(watched, reason in _KEYBOARD_REASONS)
            elif event.type() == QEvent.Type.FocusOut:
                self._mark(watched, False)
        return False  # never consume: this only observes

    @staticmethod
    def _mark(widget: QWidget, visible: bool) -> None:
        want = "true" if visible else "false"
        if widget.property("focusVisible") == want:
            return
        widget.setProperty("focusVisible", want)
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)


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

    # Apply the saved appearance before any window is constructed.  Doing it
    # here, rather than in one view, keeps dialogs and every role's workspace
    # visually consistent.
    set_theme(app, current_theme())
    # Held on the application so it lives as long as the process does.
    focus_filter = FocusVisibilityFilter(app)
    app.installEventFilter(focus_filter)
    app.setProperty("focusVisibilityFilter", focus_filter)
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
