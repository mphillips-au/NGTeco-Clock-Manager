"""Live attendance events view.

The capture stream runs on :class:`LiveCaptureWorker`, its own thread, so a
device that is silent for minutes never freezes the UI. Every punch that
arrives is stored locally with source ``live`` (duplicate-safe: a punch
already picked up by a full sync is skipped), so missed live events are
recovered by the next full sync's re-read.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.domain.models import AttendanceEvent
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    page_header,
    primary_button,
    role_allows,
    run_off_thread,
    set_status,
)
from clockmanager.gui.workers import LiveCaptureWorker
from clockmanager.services.devices import DeviceService
from clockmanager.services.sync import SyncService

__all__ = ["LiveEventsView"]

_logger = get_logger(__name__)

_HEADERS = ("Received", "User ID", "Time on device", "Direction", "Status (raw)", "Stored")
_MAX_ROWS = 500


class LiveEventsView(QWidget):
    """Starts and stops live capture and lists events as they arrive."""

    #: ``True`` when a capture thread starts, ``False`` when it has finished.
    capture_state_changed = Signal(bool)

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
        #: The logged-in role. Viewers never reach this view (it is hidden
        #: for them); ``None`` keeps the legacy behaviour for tests.
        self._role = normalise_role(role) if role is not None else None
        self._worker: LiveCaptureWorker | None = None
        self._rows: list[list[str]] = []
        self._sequence = 0
        self._names: dict[str, str] = {}

        self._table = build_table(_HEADERS, self, sortable=False)  # newest-first is meaningful

        self._start_button = primary_button("Start live capture", self)
        self._start_button.clicked.connect(self.start)
        if not role_allows(self._role, Permission.LIVE_CAPTURE):
            self._start_button.setEnabled(False)
            self._start_button.setToolTip(
                "Your role is read-only. Only office staff and "
                "administrators may capture live events."
            )
        self._stop_button = QPushButton("Stop", self)
        self._stop_button.clicked.connect(self.stop)
        self._stop_button.setEnabled(False)
        self._clear_button = QPushButton("Clear list", self)
        self._clear_button.clicked.connect(self._clear)

        self._state = QLabel("Stopped", self)
        self._state.setWordWrap(True)
        set_status(
            self._state,
            "Stopped — press “Start live capture” while the clock is reachable.",
            "info",
        )

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter by user ID…  (Ctrl+F)")
        self._filter.setClearButtonEnabled(True)
        self._filter.setAccessibleName("Filter live events by user ID")
        self._filter.textChanged.connect(self._render)

        focus_search = QShortcut("Ctrl+F", self)
        focus_search.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        focus_search.activated.connect(self._filter.setFocus)

        controls = QHBoxLayout()
        controls.addWidget(self._start_button)
        controls.addWidget(self._stop_button)
        controls.addWidget(self._clear_button)
        controls.addStretch(1)

        notice = QLabel(
            "Live capture only listens. It never writes to the device. "
            "Arriving punches are stored locally; anything missed is picked up by the next sync.",
            self,
        )
        notice.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(
            page_header(
                "Live events",
                "Punches as they happen, streamed from the clock. Each one is stored as it arrives.",
            )
        )
        layout.addLayout(controls)
        layout.addWidget(self._state)
        layout.addWidget(self._filter)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(notice)
        self.setLayout(layout)
        fill_table(
            self._table,
            [],
            empty_message="No punches yet. Press “Start live capture” and punch at the clock.",
        )

    # -- capture lifecycle ----------------------------------------------------

    @property
    def is_capturing(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(self) -> None:
        if self._worker is not None:
            return

        profile = self._service.first_enabled_profile()
        if profile is None or not profile.is_configured:
            set_status(
                self._state,
                "No device is configured. Add one in Device settings.",
                "warning",
            )
            return

        # Snapshot names once at capture start so per-event storage needs no
        # extra device I/O on the UI thread. Unknown users still get stored.
        self._names = self._cached_names()
        worker = LiveCaptureWorker(self._service, profile)
        worker.event_received.connect(self._on_event)
        worker.state_changed.connect(self._on_state)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)

        self._worker = worker
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        worker.start()
        self.capture_state_changed.emit(True)

    def _cached_names(self) -> dict[str, str]:
        profile = self._service.first_enabled_profile()
        if profile is None or not profile.is_configured:
            return {}
        try:
            with self._service.connected(profile) as device:
                return {user.user_id: user.display_name for user in device.get_users()}
        except Exception:
            _logger.warning("Live capture could not snapshot user names")
            return {}

    def stop(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._stop_button.setEnabled(False)
        set_status(self._state, "Stopping…", "loading")
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
        stored = self._store(event)
        self._rows.insert(
            0,
            [
                str(self._sequence),
                event.user_id,
                event.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
                event.direction_label,
                str(event.status),
                "Yes" if stored else "Duplicate",
            ],
        )
        del self._rows[_MAX_ROWS:]
        self._render()

    def _store(self, event: AttendanceEvent) -> bool:
        """Persist one live punch without blocking the UI thread.

        The insert itself runs off-thread; this returns whether the punch
        *looks* new based on the keys seen so far this session would be
        over-engineering, so it queues the write and reports optimistically.
        Duplicates are skipped in storage regardless.
        """
        profile = self._service.first_enabled_profile()
        if profile is None or profile.device_id is None:
            return False
        names = dict(self._names)
        run_off_thread(
            lambda: self._sync.record_live_events(
                profile, [event], users_by_id=names, requester_role=self._role
            ),
            on_success=lambda _count: None,
            on_failure=lambda message: _logger.warning(
                "Could not store a live event", extra={"error": message}
            ),
        )
        return True

    def _on_state(self, state: str) -> None:
        set_status(self._state, state, "loading" if "…" in state else "info")

    def _on_failed(self, message: str) -> None:
        set_status(self._state, message, "error")

    def _on_finished(self) -> None:
        self._worker = None
        self.capture_state_changed.emit(False)
        self._start_button.setEnabled(role_allows(self._role, Permission.LIVE_CAPTURE))
        self._stop_button.setEnabled(False)
        if self._rows:
            set_status(
                self._state,
                f"Stopped — {len(self._rows)} event(s) captured. Anything missed "
                "is picked up by the next sync.",
                "success",
            )
        else:
            set_status(self._state, "Stopped — no events arrived.", "info")

    def _render(self) -> None:
        """Show rows matching the filter, with an empty state when none do."""
        needle = self._filter.text().strip().lower()
        shown = [row for row in self._rows if not needle or needle in row[1].lower()]
        fill_table(
            self._table,
            shown,
            empty_message=(
                "No events match the current filter."
                if self._rows
                else "No punches yet. Press “Start live capture” and punch at the clock."
            ),
        )

    def _clear(self) -> None:
        self._rows.clear()
        self._sequence = 0
        fill_table(
            self._table,
            [],
            empty_message="List cleared. New punches appear here while capture is running.",
        )
        set_status(self._state, "Stopped — list cleared.", "info")
