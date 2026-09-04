"""Live attendance events view.

The capture stream runs on :class:`LiveCaptureWorker`, its own thread, so a
device that is silent for minutes never freezes the UI.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.models import AttendanceEvent
from clockmanager.gui.views.common import build_table, fill_table, section_label
from clockmanager.gui.workers import LiveCaptureWorker
from clockmanager.services.devices import DeviceService

__all__ = ["LiveEventsView"]

_logger = get_logger(__name__)

_HEADERS = ("Received", "User ID", "Time on device", "Direction", "Status (raw)")
_MAX_ROWS = 500


class LiveEventsView(QWidget):
    """Starts and stops live capture and lists events as they arrive."""

    def __init__(self, service: DeviceService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._worker: LiveCaptureWorker | None = None
        self._rows: list[list[str]] = []
        self._sequence = 0

        self._table = build_table(_HEADERS, self, sortable=False)  # newest-first is meaningful

        self._start_button = QPushButton("Start live capture", self)
        self._start_button.clicked.connect(self.start)
        self._stop_button = QPushButton("Stop", self)
        self._stop_button.clicked.connect(self.stop)
        self._stop_button.setEnabled(False)
        self._clear_button = QPushButton("Clear list", self)
        self._clear_button.clicked.connect(self._clear)

        self._state = QLabel("Stopped", self)
        self._state.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self._start_button)
        controls.addWidget(self._stop_button)
        controls.addWidget(self._clear_button)
        controls.addStretch(1)

        notice = QLabel("Live capture only listens. It never writes to the device.", self)
        notice.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Live attendance events", self))
        layout.addLayout(controls)
        layout.addWidget(self._state)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(notice)
        self.setLayout(layout)

    # -- capture lifecycle ----------------------------------------------------

    @property
    def is_capturing(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(self) -> None:
        if self._worker is not None:
            return

        profile = self._service.first_enabled_profile()
        if profile is None or not profile.is_configured:
            self._state.setText("No device is configured. Add one in Device settings.")
            return

        worker = LiveCaptureWorker(self._service, profile)
        worker.event_received.connect(self._on_event)
        worker.state_changed.connect(self._on_state)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)

        self._worker = worker
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        worker.start()

    def stop(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._stop_button.setEnabled(False)
        self._state.setText("Stopping…")
        worker.stop()

    def shutdown(self) -> None:
        """Stop capture and wait for the thread, for window close."""
        worker = self._worker
        if worker is None:
            return
        worker.stop()
        if not worker.wait(10_000):  # pragma: no cover - only on a wedged device
            _logger.warning("Live capture thread did not stop within the timeout")

    # -- signal handlers ------------------------------------------------------

    def _on_event(self, event: object) -> None:
        if not isinstance(event, AttendanceEvent):  # pragma: no cover - defensive
            return
        self._sequence += 1
        self._rows.insert(
            0,
            [
                str(self._sequence),
                event.user_id,
                event.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
                event.direction_label,
                str(event.status),
            ],
        )
        del self._rows[_MAX_ROWS:]
        fill_table(self._table, self._rows)

    def _on_state(self, state: str) -> None:
        self._state.setText(state)

    def _on_failed(self, message: str) -> None:
        self._state.setText(message)

    def _on_finished(self) -> None:
        self._worker = None
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)

    def _clear(self) -> None:
        self._rows.clear()
        self._sequence = 0
        fill_table(self._table, [])
