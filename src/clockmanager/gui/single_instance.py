"""One running copy per data folder.

Launching the application while it is already running (the desktop shortcut,
the Start Menu, Windows startup) brings the running window forward instead of
starting a second process. Two processes against one data folder would each
run their own background sync and live capture against the same clock, and
PHASE 15 showed how badly the device copes with competing sessions.

On Windows a named mutex decides which launch is first; a ``QLocalServer``
(a named pipe) is only the channel a later launch uses to say "show
yourself". Elsewhere the channel doubles as the lock: if something answers,
a copy is running.

The name is derived from the data folder, so ``--data-dir`` gives an
independent instance (useful for a test database) and two Windows accounts
never see each other.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.windows import NamedMutex, allow_any_foreground_window, is_windows

__all__ = ["ACK_MESSAGE", "ACTIVATE_MESSAGE", "SingleInstance", "instance_key"]

_logger = get_logger(__name__)

#: The only request the channel carries, and the running copy's reply. The
#: sender waits for the reply: closing straight after writing can lose the
#: message if the running copy has not accepted the connection yet.
ACTIVATE_MESSAGE = b"activate\n"
ACK_MESSAGE = b"ok\n"

#: How long a later launch keeps trying to reach the first. It covers the
#: moment between the first copy taking the mutex and it starting to listen,
#: which includes its database bootstrap.
_HANDOFF_TIMEOUT_SECONDS = 10.0
_CONNECT_TIMEOUT_MS = 500
#: How long to wait for the reply once connected. The running copy answers
#: from its event loop, so this is how long its UI may be busy.
_ACK_TIMEOUT_MS = 3000


def instance_key(data_dir: Path) -> str:
    """The pipe/mutex name for one data folder.

    Hashed because the path may contain characters a pipe name cannot, and
    case-folded on Windows where ``C:/Data`` and ``c:/data`` are one folder.
    """
    resolved = str(Path(data_dir).expanduser().resolve())
    if is_windows():
        resolved = resolved.casefold()
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    return f"NGTecoClockManager-{digest}"


class SingleInstance(QObject):
    """Claims the data folder for this process, or hands off to its owner.

    Use :meth:`acquire` before bootstrapping. The first process then calls
    :meth:`listen` once the Qt application exists and connects
    :attr:`activation_requested` to whatever brings its window forward.
    A later process calls :meth:`activate_running_instance` and exits.
    """

    activation_requested = Signal()

    def __init__(self, key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._mutex: NamedMutex | None = None
        self._server: QLocalServer | None = None

    @classmethod
    def for_data_dir(cls, data_dir: Path) -> SingleInstance:
        return cls(instance_key(data_dir))

    @property
    def key(self) -> str:
        return self._key

    def acquire(self) -> bool:
        """Whether this process is the first for its data folder."""
        mutex = NamedMutex.create(rf"Local\{self._key}")
        if mutex is not None:
            if mutex.already_existed:
                mutex.close()
                return False
            self._mutex = mutex
            return True
        # No mutex (not Windows, or the call failed): the channel is the lock.
        return not self._send(ACTIVATE_MESSAGE, attempts_until=time.monotonic())

    def listen(self) -> bool:
        """Start accepting activation requests. ``False`` if the pipe is unusable.

        A failure here is logged, not raised: the application still works,
        a second launch just opens a second window.
        """
        if self._server is not None:
            return True
        server = QLocalServer(self)
        # Only this Windows account (or Unix user) may connect.
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self._key):
            # A crashed copy can leave a stale socket file behind on Unix.
            QLocalServer.removeServer(self._key)
            if not server.listen(self._key):
                _logger.warning(
                    "Single-instance channel unavailable", extra={"error": server.errorString()}
                )
                return False
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def activate_running_instance(self) -> bool:
        """Ask the first copy to show itself. ``True`` once it has replied."""
        allow_any_foreground_window()
        delivered = self._send(
            ACTIVATE_MESSAGE, attempts_until=time.monotonic() + _HANDOFF_TIMEOUT_SECONDS
        )
        if not delivered:
            _logger.warning("A running copy holds the data folder but did not answer")
        return delivered

    def release(self) -> None:
        """Stop listening and give up the lock. Safe to call more than once."""
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._mutex is not None:
            self._mutex.close()
            self._mutex = None

    # -- channel ----------------------------------------------------------------

    def _send(self, message: bytes, *, attempts_until: float) -> bool:
        """Deliver ``message`` and wait for the reply.

        Connecting is retried until ``attempts_until`` passes; a copy that
        accepts the connection but never replies counts as not answering.
        """
        while True:
            socket = QLocalSocket()
            socket.connectToServer(self._key)
            if socket.waitForConnected(_CONNECT_TIMEOUT_MS):
                socket.write(message)
                socket.flush()
                replied = self._wait_for_ack(socket)
                socket.disconnectFromServer()
                return replied
            socket.abort()
            if time.monotonic() >= attempts_until:
                return False
            time.sleep(0.2)

    @staticmethod
    def _wait_for_ack(socket: QLocalSocket) -> bool:
        received = b""
        deadline = time.monotonic() + _ACK_TIMEOUT_MS / 1000
        while ACK_MESSAGE.strip() not in received:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0 or not socket.waitForReadyRead(remaining_ms):
                return False
            received += bytes(socket.readAll().data())
        return True

    def _on_new_connection(self) -> None:
        server = self._server
        if server is None:  # pragma: no cover - closed between signal and slot
            return
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connection.readyRead.connect(lambda c=connection: self._read(c))
            connection.disconnected.connect(connection.deleteLater)
            if connection.bytesAvailable():
                self._read(connection)

    def _read(self, connection: QLocalSocket) -> None:
        data = bytes(connection.readAll().data())
        if ACTIVATE_MESSAGE.strip() in data:
            connection.write(ACK_MESSAGE)
            connection.flush()
            self.activation_requested.emit()
