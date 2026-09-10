"""Close-to-tray and single-instance behaviour.

The offscreen test platform has no notification area, so close-to-tray is
switched on directly on the window; what is under test is what the window,
the session and the single-instance channel do, not the platform's tray.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from clockmanager.gui.app import GuiSession
from clockmanager.gui.main_window import MainWindow
from clockmanager.gui.single_instance import ACTIVATE_MESSAGE, SingleInstance, instance_key
from clockmanager.gui.tray import (
    TrayIcon,
    close_to_tray_enabled,
    set_close_to_tray_enabled,
)
from clockmanager.services.application import ApplicationContext
from clockmanager.windows import is_windows
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui

_PREFERENCE_KEY = "window/close_to_tray"


@pytest.fixture
def preserved_preference() -> Iterator[None]:
    """Tests write the real per-user preference; put it back afterwards."""
    settings = QSettings()
    had_value = settings.contains(_PREFERENCE_KEY)
    original = settings.value(_PREFERENCE_KEY)
    try:
        yield
    finally:
        if had_value:
            settings.setValue(_PREFERENCE_KEY, original)
        else:
            settings.remove(_PREFERENCE_KEY)


def _unique_key() -> str:
    return f"NGTecoClockManager-test-{uuid.uuid4().hex[:12]}"


def _pump_until(qt_app: QApplication, condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


#: A later launch reduced to the handoff. It runs as a separate process
#: because that is the real situation, and Qt's pipe I/O needs a thread
#: Qt owns.
_HAND_OFF_CODE = (
    "import sys; "
    "from clockmanager.gui.single_instance import SingleInstance; "
    "sys.exit(0 if SingleInstance(sys.argv[1]).activate_running_instance() else 1)"
)


def _hand_off(key: str) -> subprocess.Popen[bytes]:
    return _launch(["-c", _HAND_OFF_CODE, key])


def _launch(args: list[str]) -> subprocess.Popen[bytes]:
    """Start a later launch as its own process (offscreen, like the tests)."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    return subprocess.Popen(
        [sys.executable, *args],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _recorder(signal: object) -> list[bool]:
    seen: list[bool] = []
    signal.connect(lambda *_: seen.append(True))  # type: ignore[attr-defined]
    return seen


# -- the window -----------------------------------------------------------------


def test_close_hides_to_tray_and_keeps_background_work(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    window = MainWindow(mock_context)
    hidden = _recorder(window.hidden_to_tray)
    closed = _recorder(window.closed)
    window.set_close_to_tray(True)
    window.show()
    try:
        drain(qt_app)
        window.close()
        assert not window.isVisible()
        assert hidden == [True]
        assert closed == []
        # Background sync is still scheduled: hiding is not shutting down.
        assert window._background_timer.isActive()

        window.bring_to_front()
        assert window.isVisible()
    finally:
        window.request_quit()
    assert closed == [True]
    assert not window._background_timer.isActive()


def test_close_without_tray_closes_as_before(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    window = MainWindow(mock_context)
    closed = _recorder(window.closed)
    window.show()
    drain(qt_app)
    window.close()
    assert closed == [True]
    assert not window._background_timer.isActive()


def test_file_exit_quits_even_with_close_to_tray(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    window = MainWindow(mock_context)
    closed = _recorder(window.closed)
    window.set_close_to_tray(True)
    window.show()
    drain(qt_app)
    file_menu = window.menuBar().actions()[0].menu()
    exit_action = next(action for action in file_menu.actions() if action.text() == "E&xit")
    exit_action.trigger()
    assert closed == [True]


def test_logout_closes_for_real_with_close_to_tray(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    window = MainWindow(mock_context)
    closed = _recorder(window.closed)
    window.set_close_to_tray(True)
    window.show()
    drain(qt_app)
    window._logout()
    assert window.logout_requested
    assert closed == [True]


# -- the session the tray acts on ------------------------------------------------


def test_session_quit_closes_a_hidden_window(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    session = GuiSession(qt_app, tray=None)
    window = MainWindow(mock_context)
    window.set_close_to_tray(True)
    session.window = window
    closed = _recorder(window.closed)
    window.show()
    drain(qt_app)
    window.close()  # hidden, still running
    assert closed == []

    session.bring_to_front()
    assert window.isVisible()

    session.quit()
    assert session.quit_requested
    assert closed == [True]


def test_session_ending_lets_the_close_through(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    """Windows logoff must never be vetoed by a window that only hides."""
    session = GuiSession(qt_app, tray=None)
    window = MainWindow(mock_context)
    window.set_close_to_tray(True)
    session.window = window
    closed = _recorder(window.closed)
    window.show()
    drain(qt_app)
    session.on_session_ending(None)  # type: ignore[arg-type]
    window.close()
    assert closed == [True]
    assert session.quit_requested


# -- tray icon and preference ----------------------------------------------------


def test_tray_tooltip_tracks_sign_in_and_capture(qt_app: QApplication) -> None:
    tray = TrayIcon(qt_app.windowIcon())
    assert "Not signed in" in tray.toolTip()
    tray.set_signed_in("Boss")
    assert "Signed in as Boss" in tray.toolTip()
    tray.set_capturing(True)
    assert "Live capture running" in tray.toolTip()
    tray.set_signed_in(None)
    assert "Not signed in" in tray.toolTip()
    assert len(tray.toolTip()) <= 127


def test_tray_menu_emits_open_and_quit(qt_app: QApplication) -> None:
    tray = TrayIcon(qt_app.windowIcon())
    seen: list[str] = []
    tray.open_requested.connect(lambda: seen.append("open"))
    tray.quit_requested.connect(lambda: seen.append("quit"))
    tray.open_action.trigger()
    tray.quit_action.trigger()
    assert seen == ["open", "quit"]


def test_close_to_tray_preference_round_trips(preserved_preference: None) -> None:
    set_close_to_tray_enabled(False)
    assert close_to_tray_enabled() is False
    set_close_to_tray_enabled(True)
    assert close_to_tray_enabled() is True


def test_settings_toggle_applies_to_the_open_window(
    qt_app: QApplication, mock_context: ApplicationContext, preserved_preference: None
) -> None:
    window = MainWindow(mock_context)
    try:
        checkbox = window.settings_view._close_to_tray
        checkbox.setChecked(True)
        checkbox.setChecked(False)
        assert close_to_tray_enabled() is False
        assert window.close_to_tray is False
    finally:
        window.request_quit()


# -- single instance ---------------------------------------------------------------


def test_instance_key_is_per_folder(tmp_path: Path) -> None:
    first = instance_key(tmp_path / "one")
    assert first == instance_key(tmp_path / "one")
    assert first != instance_key(tmp_path / "two")
    if is_windows():
        assert first == instance_key(Path(str(tmp_path / "one").upper()))


def test_second_instance_hands_off_to_the_first(qt_app: QApplication) -> None:
    key = _unique_key()
    first = SingleInstance(key)
    activations = _recorder(first.activation_requested)
    try:
        assert first.acquire()
        assert first.listen()
        assert not SingleInstance(key).acquire()
        later = _hand_off(key)
        assert _pump_until(qt_app, lambda: later.poll() is not None, timeout=15.0)
        assert later.returncode == 0
        assert activations == [True]
    finally:
        first.release()


def test_handoff_survives_a_busy_running_copy(qt_app: QApplication) -> None:
    """The running copy may be mid-task when the shortcut is clicked; the
    later launch waits for its reply rather than writing and leaving."""
    key = _unique_key()
    first = SingleInstance(key)
    activations = _recorder(first.activation_requested)
    try:
        assert first.acquire()
        assert first.listen()
        later = _hand_off(key)
        time.sleep(1.5)  # the UI thread is busy: no events are processed
        assert _pump_until(qt_app, lambda: later.poll() is not None, timeout=15.0)
        assert later.returncode == 0
        assert activations == [True]
    finally:
        first.release()


def test_unanswered_handoff_gives_up(qt_app: QApplication) -> None:
    """Nobody listening: the later launch reports failure instead of hanging."""
    lonely = SingleInstance(_unique_key())
    started = time.monotonic()
    assert lonely._send(ACTIVATE_MESSAGE, attempts_until=time.monotonic()) is False
    assert time.monotonic() - started < 5


def test_released_instance_frees_the_folder(qt_app: QApplication) -> None:
    key = _unique_key()
    first = SingleInstance(key)
    assert first.acquire()
    first.listen()
    first.release()
    again = SingleInstance(key)
    try:
        assert again.acquire()
    finally:
        again.release()


def test_launch_while_running_exits_without_touching_the_database(
    qt_app: QApplication, tmp_path: Path
) -> None:
    data_dir = tmp_path / "appdata"
    running = SingleInstance.for_data_dir(data_dir)
    activations = _recorder(running.activation_requested)
    try:
        assert running.acquire()
        assert running.listen()
        later = _launch(["-m", "clockmanager", "--data-dir", str(data_dir)])
        assert _pump_until(qt_app, lambda: later.poll() is not None, timeout=30.0)
        assert later.returncode == 0
        assert activations == [True]
        assert not data_dir.exists()  # no bootstrap, no database, no log
    finally:
        running.release()
