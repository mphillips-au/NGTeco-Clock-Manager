"""The notification-area icon.

Closing the window hides it here instead of quitting, so live capture and
background sync keep running with the window out of the way. Quitting is a
deliberate act: Quit on this icon's menu, or File > Exit.

Whether the close button hides or quits is remembered per computer, the same
way the theme is (``QSettings``), and changes take effect immediately.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from clockmanager import APPLICATION_NAME

__all__ = [
    "HIDDEN_NOTICE",
    "TrayIcon",
    "close_to_tray_enabled",
    "set_close_to_tray_enabled",
    "tray_supported",
]

_CLOSE_TO_TRAY_KEY = "window/close_to_tray"

#: The notice shown the first time the window is hidden in a session.
HIDDEN_NOTICE = (
    "Still running in the notification area. Background sync and live capture "
    "carry on. Right-click this icon and choose Quit to exit."
)


def tray_supported() -> bool:
    """Whether this desktop has a notification area to hide into."""
    return QSystemTrayIcon.isSystemTrayAvailable()


def close_to_tray_enabled() -> bool:
    """The stored preference. On by default: that is the point of the icon."""
    stored = QSettings().value(_CLOSE_TO_TRAY_KEY, True)
    return str(stored).lower() not in {"false", "0", "no"}


def set_close_to_tray_enabled(enabled: bool) -> None:
    QSettings().setValue(_CLOSE_TO_TRAY_KEY, bool(enabled))


class TrayIcon(QSystemTrayIcon):
    """Open and Quit, plus a tooltip that says what is running."""

    open_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon: QIcon, parent: QWidget | None = None) -> None:
        super().__init__(icon, parent)
        self._signed_in: str | None = None
        self._capturing = False
        self._hidden_notice_shown = False

        # The menu has no parent widget, so it is kept alive here.
        self._menu = QMenu()
        self.open_action = QAction(f"Open {APPLICATION_NAME}", self._menu)
        font = self.open_action.font()
        font.setBold(True)  # the default action, as Windows convention shows it
        self.open_action.setFont(font)
        self.open_action.triggered.connect(self.open_requested.emit)
        self.quit_action = QAction("Quit", self._menu)
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(self.open_action)
        self._menu.addSeparator()
        self._menu.addAction(self.quit_action)
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)
        self._refresh_tooltip()

    @classmethod
    def for_application(cls, app: QApplication) -> TrayIcon:
        return cls(app.windowIcon())

    def set_signed_in(self, name: str | None) -> None:
        self._signed_in = name
        if name is None:
            self._capturing = False
        self._refresh_tooltip()

    def set_capturing(self, capturing: bool) -> None:
        self._capturing = capturing
        self._refresh_tooltip()

    def notify_hidden(self) -> None:
        """Explain where the window went, once per session."""
        if self._hidden_notice_shown:
            return
        self._hidden_notice_shown = True
        if self.supportsMessages():
            self.showMessage(
                APPLICATION_NAME, HIDDEN_NOTICE, QSystemTrayIcon.MessageIcon.Information, 6000
            )

    def _refresh_tooltip(self) -> None:
        if self._signed_in is None:
            detail = "Not signed in"
        elif self._capturing:
            detail = f"Live capture running · {self._signed_in}"
        else:
            detail = f"Signed in as {self._signed_in}"
        # Windows truncates tray tooltips at 127 characters.
        self.setToolTip(f"{APPLICATION_NAME}\n{detail}"[:127])

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_requested.emit()
