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

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.domain.models import PunchDirection
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    muted_label,
    notify,
    page_header,
    primary_button,
    role_allows,
    run_off_thread,
    set_status,
    tint_cell,
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

        # The employee name absorbs spare width; identifiers, timestamps and
        # flags stay at their natural size so the row reads left to right.
        self._table = build_table(_HEADERS, self, stretch_columns=(1,))
        self._table.itemSelectionChanged.connect(self._show_details)
        self._table.setAccessibleName("Stored attendance punches")

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter by user ID or name…  (Ctrl+F)")
        self._filter.setClearButtonEnabled(True)
        self._filter.setAccessibleName("Filter attendance by user ID or name")
        self._filter.textChanged.connect(self._apply_filter)

        focus_search = QShortcut("Ctrl+F", self)
        focus_search.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        focus_search.activated.connect(self._filter.setFocus)

        self._direction = QComboBox(self)
        self._direction.addItem("All directions", "")
        self._direction.addItem("IN", "IN")
        self._direction.addItem("OUT", "OUT")
        self._direction.addItem("Unknown punch", "unknown")
        self._direction.setAccessibleName("Filter by punch direction")
        self._direction.currentIndexChanged.connect(self._apply_filter)

        self._today_only = QCheckBox("Today only", self)
        self._today_only.toggled.connect(self._apply_filter)

        self._details = QLabel("Select a punch to see its details.", self)
        self._details.setWordWrap(True)
        self._details.setAccessibleName("Selected punch details")
        detail_form = QVBoxLayout()
        detail_form.addWidget(self._details)
        detail_note = muted_label(
            "Status is raw device metadata and is never interpreted. "
            "Direction comes from the punch value alone.",
            self,
        )
        detail_form.addWidget(detail_note)
        detail_group = QGroupBox("Punch details", self)
        detail_group.setLayout(detail_form)

        self._sync_button = primary_button("Sync now", self)
        self._sync_button.clicked.connect(self.sync_now)
        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.clicked.connect(self.load)
        self._refresh_button.setShortcut("F5")
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
        controls.addWidget(self._direction)
        controls.addWidget(self._today_only)

        splitter = QSplitter(self)
        splitter.setOrientation(Qt.Orientation.Vertical)
        table_host = QWidget(self)
        table_layout = QVBoxLayout()
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self._table, stretch=1)
        table_host.setLayout(table_layout)
        splitter.addWidget(table_host)
        splitter.addWidget(detail_group)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout()
        layout.addWidget(
            page_header(
                "Attendance",
                "Punches stored on this computer. Everything here works offline; "
                "“Sync now” adds anything new from the clock.",
            )
        )
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(splitter, stretch=1)
        self.setLayout(layout)
        fill_table(
            self._table,
            [],
            empty_message="Loading stored attendance…",
        )

    def load(self) -> None:
        """Load stored events off the UI thread."""
        self._refresh_button.setEnabled(False)
        set_status(self._status, "Loading stored attendance…", "loading")
        run_off_thread(
            self._read_stored,
            on_success=self._on_loaded,
            on_failure=self._on_failure,
        )

    def sync_now(self) -> None:
        """Run a manual sync off the UI thread, then refresh from storage."""
        self._sync_button.setEnabled(False)
        set_status(self._status, "Syncing… reading the device history.", "loading")
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
            set_status(
                self._status,
                "No device is configured. Add one in Device settings.",
                "warning",
            )
            fill_table(
                self._table,
                [],
                empty_message="No device configured. Open Device settings to add one.",
            )
            return
        if not isinstance(payload, tuple):  # pragma: no cover - defensive
            return
        events, summary = payload
        self._events = events
        if not events:
            set_status(
                self._status,
                "No punches stored yet. Press “Sync now” while the clock is reachable; "
                "everything keeps working offline afterwards.",
                "info",
            )
        else:
            set_status(self._status, summary or self._summarise(events), "info")
        self._apply_filter()

    def _on_synced(self, result: Any) -> None:
        self._sync_button.setEnabled(role_allows(self._role, Permission.SYNC_ATTENDANCE))
        if result is None:
            set_status(
                self._status,
                "No device is configured. Add one in Device settings.",
                "warning",
            )
            return
        if not isinstance(result, SyncResult):  # pragma: no cover - defensive
            return
        set_status(
            self._status,
            result.summary + " Refreshing…",
            "success" if result.ok else "error",
        )
        notify(self, result.summary, kind="success" if result.ok else "error")
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
        from datetime import date as _date

        needle = self._filter.text().strip().lower()
        direction = str(self._direction.currentData() or "")
        today_only = self._today_only.isChecked()
        today = _date.today()  # noqa: DTZ011 - device-local business day, not a timestamp
        shown: list[StoredAttendance] = []
        rows = []
        for event in self._events:
            if needle and (
                needle not in event.user_id.lower()
                and not (event.employee_name and needle in event.employee_name.lower())
            ):
                continue
            if direction == "unknown":
                if event.punch in (PunchDirection.IN, PunchDirection.OUT):
                    continue
            elif direction and event.direction_label != direction:
                continue
            if today_only and event.occurred_at.date() != today:
                continue
            shown.append(event)
            rows.append(
                [
                    event.user_id,
                    event.employee_name or "",
                    event.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
                    event.direction_label,
                    str(event.status),
                    event.source,
                    "" if event.device_uid is None else str(event.device_uid),
                ]
            )
        fill_table(
            self._table,
            rows,
            empty_message=(
                "No punches match these filters. Clear the search, or switch off “Today only”."
                if self._events
                else "No punches stored yet. Press “Sync now” while the clock is reachable."
            ),
        )
        # IN and OUT are the two values an operator scans for; colour makes
        # the alternation visible without reading every row.
        for index, event in enumerate(shown):
            token = "badge_in" if event.punch == PunchDirection.IN else "badge_out"
            tint_cell(self._table, index, 3, token)
        if self._events and not shown:
            set_status(
                self._status,
                "No punches match the current filters. Adjust the search or filters.",
                "info",
            )
        self._show_details()

    def _show_details(self) -> None:
        """Describe the selected punch from its displayed cells."""
        row = self._table.currentRow()
        get = self._table.item
        if row < 0 or get(row, 0) is None:
            self._details.setText("Select a punch to see its details.")
            return
        cells: list[str] = []
        for column in range(7):
            item = get(row, column)
            cells.append(item.text() if item is not None else "")
        self._details.setText(
            f"User ID {cells[0]}"
            + (f" ({cells[1]})" if cells[1] else "")
            + f" — {cells[3]} at {cells[2]} "
            + f"(status {cells[4]}, source {cells[5]}"
            + (f", UID {cells[6]}" if cells[6] else "")
            + ")."
        )

    def _on_failure(self, message: str) -> None:
        self._sync_button.setEnabled(role_allows(self._role, Permission.SYNC_ATTENDANCE))
        self._refresh_button.setEnabled(True)
        set_status(self._status, message, "error")
        notify(self, message, kind="error")
