"""Background workers.

``AGENTS.md`` requires that GUI I/O never blocks the UI thread, and PHASE 02
requires the same of all device and network I/O. Every device call in the GUI
goes through one of these:

* :class:`CallableWorker` for one-shot operations on the thread pool.
* :class:`LiveCaptureWorker` for the long-lived live-attendance stream, which
  needs its own thread rather than a pooled slot.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThread, Signal

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.models import AttendanceEvent
from clockmanager.errors import ClockManagerError
from clockmanager.services.devices import DeviceProfile, DeviceService

__all__ = ["CallableWorker", "LiveCaptureWorker", "WorkerSignals"]

_logger = get_logger(__name__)


class WorkerSignals(QObject):
    """Signals emitted by :class:`CallableWorker`."""

    finished = Signal(object)
    failed = Signal(str)


class CallableWorker(QRunnable):
    """Run a callable on a thread pool and report the result via signals.

    The failure signal carries a message only. Exception detail goes to the
    redacting logger, never to the widget layer.
    """

    def __init__(self, work: Callable[[], Any]) -> None:
        super().__init__()
        self._work = work
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self._work()
        except Exception as exc:
            _logger.exception("Background task failed")
            self.signals.failed.emit(str(exc))
        else:
            self.signals.finished.emit(result)


class LiveCaptureWorker(QThread):
    """Streams live attendance events off the UI thread.

    The device connection is opened, used and closed entirely on this thread.
    :meth:`stop` asks the stream to finish; the device's own idle timeout means
    it never blocks indefinitely waiting for a punch.
    """

    event_received = Signal(object)
    state_changed = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        service: DeviceService,
        profile: DeviceProfile,
        *,
        idle_timeout_seconds: float = 2.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._profile = profile
        self._idle_timeout_seconds = idle_timeout_seconds
        self._stopping = False

    def stop(self) -> None:
        """Ask the capture loop to finish. Safe to call from the UI thread."""
        self._stopping = True

    def run(self) -> None:
        device = None
        try:
            self.state_changed.emit("Connecting…")
            device = self._service.open_device(self._profile)
            self.state_changed.emit("Listening for punches")

            for event in device.live_capture(timeout_seconds=self._idle_timeout_seconds):
                if self._stopping:
                    break
                if isinstance(event, AttendanceEvent):
                    self.event_received.emit(event)

            device.stop_live_capture()
        except ClockManagerError as exc:
            _logger.warning(
                "Live capture failed",
                extra={"device": self._profile.name, "error": str(exc)},
            )
            self.failed.emit(str(exc))
        except Exception as exc:
            _logger.exception("Live capture failed unexpectedly")
            self.failed.emit(str(exc))
        finally:
            if device is not None:
                device.disconnect()
            self.state_changed.emit("Stopped")
