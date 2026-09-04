"""Attendance list view.

Read-only. Historical synchronisation and reconciliation arrive in PHASE 04;
this build reads and displays what is currently on the device.

Direction comes from ``punch`` alone. ``status`` is shown verbatim as raw
device metadata and is never interpreted.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.domain.models import AttendanceEvent, PunchDirection
from clockmanager.gui.views.common import build_table, fill_table, run_off_thread, section_label
from clockmanager.services.devices import DeviceProfile, DeviceService

__all__ = ["AttendanceView"]

_HEADERS = ("User ID", "Date and time", "Direction", "Status (raw)", "Device UID")


class AttendanceView(QWidget):
    """Displays attendance records stored on the device."""

    def __init__(self, service: DeviceService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._events: list[AttendanceEvent] = []

        self._table = build_table(_HEADERS, self)

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter by user ID…")
        self._filter.textChanged.connect(self._apply_filter)

        self._load_button = QPushButton("Read attendance from device", self)
        self._load_button.clicked.connect(self.load)

        self._status = QLabel("Press “Read attendance from device” to load.", self)
        self._status.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self._load_button)
        controls.addWidget(self._filter, stretch=1)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Attendance on device", self))
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        self.setLayout(layout)

    def load(self) -> None:
        """Read attendance off the UI thread."""
        self._load_button.setEnabled(False)
        self._status.setText("Reading attendance… this can take a while on a busy device.")
        run_off_thread(
            self._read_attendance,
            on_success=self._on_loaded,
            on_failure=self._on_failure,
        )

    def _read_attendance(self) -> list[AttendanceEvent] | None:
        profile: DeviceProfile | None = self._service.first_enabled_profile()
        if profile is None or not profile.is_configured:
            return None
        return self._service.read_attendance(profile)

    def _on_loaded(self, events: Any) -> None:
        self._load_button.setEnabled(True)
        if events is None:
            self._status.setText("No device is configured. Add one in Device settings.")
            fill_table(self._table, [])
            return
        if not isinstance(events, list):  # pragma: no cover - defensive
            return

        self._events = events
        ins = sum(1 for event in events if event.direction is PunchDirection.IN)
        outs = sum(1 for event in events if event.direction is PunchDirection.OUT)
        unknown = len(events) - ins - outs
        summary = f"{len(events)} record(s): {ins} IN, {outs} OUT"
        if unknown:
            summary += f", {unknown} with an unrecognised punch value"
        self._status.setText(summary)
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self._filter.text().strip().lower()
        rows = [
            [
                event.user_id,
                event.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
                event.direction_label,
                str(event.status),
                "" if event.device_uid is None else str(event.device_uid),
            ]
            for event in self._events
            if not needle or needle in event.user_id.lower()
        ]
        fill_table(self._table, rows)

    def _on_failure(self, message: str) -> None:
        self._load_button.setEnabled(True)
        self._status.setText(message)
