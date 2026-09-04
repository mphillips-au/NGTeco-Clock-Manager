"""Attendance list view.

PHASE 04: shows locally stored attendance (the sync database), not a live
device read. "Sync now" pulls the device history into local storage with
duplicate-safe reconciliation; the table then refreshes from the database so
it works offline.

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

from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.domain.models import PunchDirection
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    role_allows,
    run_off_thread,
    section_label,
)
from clockmanager.services.devices import DeviceProfile, DeviceService
from clockmanager.services.sync import StoredAttendance, SyncResult, SyncService

__all__ = ["AttendanceView"]

_HEADERS = ("User ID", "Employee", "Date and time", "Direction", "Status (raw)", "Source", "UID")


class AttendanceView(QWidget):
    """Displays locally stored attendance with manual sync."""

    def __init__(
        self,
        service: DeviceService,
        sync: SyncService,
        parent: QWidget | None = None,
        *,
        role: Role | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._sync = sync
        #: The logged-in role. Viewers cannot sync; ``None`` keeps the
        #: legacy behaviour for tests.
        self._role = normalise_role(role) if role is not None else None
        self._events: list[StoredAttendance] = []

        self._table = build_table(_HEADERS, self)

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter by user ID or name…")
        self._filter.textChanged.connect(self._apply_filter)

        self._sync_button = QPushButton("Sync now", self)
        self._sync_button.clicked.connect(self.sync_now)
        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.clicked.connect(self.load)
        if not role_allows(self._role, Permission.SYNC_ATTENDANCE):
            self._sync_button.setEnabled(False)
            self._sync_button.setToolTip(
                "Your role is read-only. Only office staff and administrators may sync."
            )

        self._status = QLabel("Press “Sync now” to store the device history locally.", self)
        self._status.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self._sync_button)
        controls.addWidget(self._refresh_button)
        controls.addWidget(self._filter, stretch=1)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Attendance (stored locally)", self))
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        self.setLayout(layout)

    def load(self) -> None:
        """Load stored events off the UI thread."""
        self._refresh_button.setEnabled(False)
        run_off_thread(
            self._read_stored,
            on_success=self._on_loaded,
            on_failure=self._on_failure,
        )

    def sync_now(self) -> None:
        """Run a manual sync off the UI thread, then refresh from storage."""
        self._sync_button.setEnabled(False)
        self._status.setText("Syncing… reading the device history.")
        run_off_thread(
            self._run_sync,
            on_success=self._on_synced,
            on_failure=self._on_failure,
        )

    def _profile(self) -> DeviceProfile | None:
        profile = self._service.first_enabled_profile()
        if profile is None or not profile.is_configured or profile.device_id is None:
            return None
        return profile

    def _read_stored(self) -> tuple[list[StoredAttendance], str] | None:
        profile = self._profile()
        if profile is None:
            return None
        return (self._sync.list_stored(profile), self._sync.summary(profile).describe())

    def _run_sync(self) -> SyncResult | None:
        profile = self._profile()
        if profile is None:
            return None
        return self._sync.manual_sync(profile, requester_role=self._role)

    def _on_loaded(self, payload: Any) -> None:
        self._refresh_button.setEnabled(True)
        if payload is None:
            self._status.setText("No device is configured. Add one in Device settings.")
            fill_table(self._table, [])
            return
        if not isinstance(payload, tuple):  # pragma: no cover - defensive
            return
        events, summary = payload
        self._events = events
        self._status.setText(summary or self._summarise(events))
        self._apply_filter()

    def _on_synced(self, result: Any) -> None:
        self._sync_button.setEnabled(role_allows(self._role, Permission.SYNC_ATTENDANCE))
        if result is None:
            self._status.setText("No device is configured. Add one in Device settings.")
            return
        if not isinstance(result, SyncResult):  # pragma: no cover - defensive
            return
        self._status.setText(result.summary + " Refreshing…")
        self.load()

    def _summarise(self, events: list[StoredAttendance]) -> str:
        ins = sum(1 for event in events if event.punch == PunchDirection.IN)
        outs = sum(1 for event in events if event.punch == PunchDirection.OUT)
        unknown = len(events) - ins - outs
        summary = f"{len(events)} stored punch(es): {ins} IN, {outs} OUT"
        if unknown:
            summary += f", {unknown} with an unrecognised punch value"
        return summary

    def _apply_filter(self) -> None:
        needle = self._filter.text().strip().lower()
        rows = [
            [
                event.user_id,
                event.employee_name or "",
                event.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
                event.direction_label,
                str(event.status),
                event.source,
                "" if event.device_uid is None else str(event.device_uid),
            ]
            for event in self._events
            if not needle
            or needle in event.user_id.lower()
            or (event.employee_name and needle in event.employee_name.lower())
        ]
        fill_table(self._table, rows)

    def _on_failure(self, message: str) -> None:
        self._sync_button.setEnabled(role_allows(self._role, Permission.SYNC_ATTENDANCE))
        self._refresh_button.setEnabled(True)
        self._status.setText(message)
