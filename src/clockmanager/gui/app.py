"""Qt application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QIcon, QSessionManager
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from clockmanager import APPLICATION_NAME, __version__
from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.gui.main_window import MainWindow
from clockmanager.gui.single_instance import SingleInstance
from clockmanager.gui.theme import current_theme, set_theme
from clockmanager.gui.tray import TrayIcon, close_to_tray_enabled, tray_supported
from clockmanager.gui.views.auth import BootstrapAdminDialog, LoginDialog
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser
from clockmanager.windows import INSTALLER_MUTEX_NAME, NamedMutex

__all__ = ["FocusVisibilityFilter", "GuiSession", "build_application", "run_gui"]

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


class GuiSession:
    """What the tray icon and a second launch act on, across logins.

    The login loop replaces the main window at every Logout; this object is
    the one fixed point that knows which window (or login dialog) is current,
    and whether the operator has asked to quit outright.
    """

    def __init__(self, app: QApplication, tray: TrayIcon | None) -> None:
        self._app = app
        self.tray = tray
        self.window: MainWindow | None = None
        self.quit_requested = False

    def attach(self, window: MainWindow, user: AuthenticatedUser) -> None:
        self.window = window
        window.set_close_to_tray(self.tray is not None and close_to_tray_enabled())
        # With a tray icon the application no longer quits when its last
        # window closes (hiding is not closing), so a real close ends the
        # event loop explicitly.
        window.closed.connect(self._on_window_closed)
        if self.tray is not None:
            window.hidden_to_tray.connect(self.tray.notify_hidden)
            window.live_view.capture_state_changed.connect(self.tray.set_capturing)
            self.tray.set_signed_in(user.display_name or user.username)

    def detach(self) -> None:
        self.window = None
        if self.tray is not None:
            self.tray.set_signed_in(None)

    def bring_to_front(self) -> None:
        """Show whatever the operator should see now: a dialog, or the window."""
        modal = self._app.activeModalWidget()
        if modal is not None:
            modal.show()
            modal.raise_()
            modal.activateWindow()
            return
        if self.window is not None:
            self.window.bring_to_front()

    def quit(self) -> None:
        """Quit from anywhere: signed in, hidden, or at the login dialog."""
        self.quit_requested = True
        modal = self._app.activeModalWidget()
        if isinstance(modal, QDialog):
            modal.reject()
        if self.window is not None:
            self.window.request_quit()

    def on_session_ending(self, _manager: QSessionManager) -> None:
        """Windows is logging off or shutting down: never veto it by hiding."""
        self.quit_requested = True
        if self.window is not None:
            self.window.prepare_to_quit()

    def _on_window_closed(self) -> None:
        self._app.exit(0)


def run_gui(
    context: ApplicationContext,
    argv: list[str] | None = None,
    *,
    instance: SingleInstance | None = None,
) -> int:
    """Log in, show the main window and run the Qt event loop.

    Choosing Logout closes the window and returns to the login dialog, so
    one process can serve consecutive operators with an audit trail for
    each session. Where the desktop has a notification area, closing the
    window hides it there and the process keeps running; ``instance``, when
    given, lets a second launch bring this one forward.
    """
    app = build_application(argv)
    # Held for the life of the process so the installer can tell a copy is
    # running, including one hidden in the notification area.
    installer_mutex = NamedMutex.create(INSTALLER_MUTEX_NAME)

    tray: TrayIcon | None = None
    if tray_supported():
        tray = TrayIcon.for_application(app)
        app.setQuitOnLastWindowClosed(False)
    session = GuiSession(app, tray)
    app.commitDataRequest.connect(session.on_session_ending)
    if tray is not None:
        tray.open_requested.connect(session.bring_to_front)
        tray.quit_requested.connect(session.quit)
        tray.show()
    if instance is not None:
        instance.activation_requested.connect(session.bring_to_front)
        instance.listen()

    try:
        while True:
            user = _ensure_login(context)
            if user is None or session.quit_requested:
                return 0
            window = MainWindow(context, current_user=user)
            session.attach(window, user)
            window.show()
            _logger.info("GUI started", extra={"actor": user.username, "role": user.role.value})
            app.exec()
            session.detach()
            if session.quit_requested or not window.logout_requested:
                return 0
            context.auth.logout()
    finally:
        if tray is not None:
            tray.hide()
        if installer_mutex is not None:
            installer_mutex.close()
