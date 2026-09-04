"""Dashboard view: the at-a-glance state of the business day.

Shows the active device and application status (as before), plus the daily
business overview operators actually need: clock state, who is currently
in/out, recent punches, today's counts, sync state and exceptions.

Everything here reads local storage only, so the dashboard works offline;
device contact happens only through the explicit "Check device connection"
and "Sync now" buttons. Failures are reported as states, never raised.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.domain.models import PunchDirection
from clockmanager.gui.icons import avatar_pixmap
from clockmanager.gui.theme import current_palette
from clockmanager.gui.views.charts import ChartSeries, TrendChart
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    notify,
    page_header,
    primary_button,
    role_allows,
    run_off_thread,
    set_status,
    tint_cell,
)
from clockmanager.services.application import ApplicationContext, ApplicationStatus
from clockmanager.services.devices import ConnectionTestResult, DeviceProfile, DeviceService

__all__ = ["DashboardView"]

_RECENT_LIMIT = 8
#: Days of history on the dashboard trend.
_TREND_DAYS = 7


def _short_when(moment: Any) -> str:
    """Render a timestamp the way an operator reads a clock, not a log.

    "today 09:24" beats "2026-09-04 09:24:01+00:00" on a status card; the
    precise value is still one hover away and in the Device panel below.
    """
    if moment is None:
        return "never"
    try:
        local = moment.astimezone()
        today = date.today()  # noqa: DTZ011 - business day, not a timestamp
        days = (today - local.date()).days
        if days == 0:
            return f"today {local:%H:%M}"
        if days == 1:
            return f"yesterday {local:%H:%M}"
        return f"{local:%d %b %H:%M}"
    except (AttributeError, ValueError, OSError):  # pragma: no cover - defensive
        return str(moment)


@dataclass(frozen=True, slots=True)
class _DashboardData:
    profile: DeviceProfile | None
    status: ApplicationStatus
    clock_state: str
    clock_hint: str
    clock_detail: str
    in_now: int
    out_now: int
    punches_today: int
    sync_state: str
    sync_hint: str
    sync_detail: str
    exceptions: str
    recent_rows: list[list[str]]
    trend_labels: list[str]
    trend_in: list[int]
    trend_out: list[int]


#: Card height is fixed so a long hint on one card cannot push its heading
#: out of line with the other three. Overflow is carried by the tooltip.
_CARD_HEIGHT = 118


def _stat_card(title: str, parent: QWidget) -> tuple[QGroupBox, QLabel, QLabel]:
    """One small status card. Returns (group, value label, hint label).

    Title, value and hint sit at fixed positions in every card so the row
    reads as one strip of figures rather than four independently sized boxes.
    """
    title_label = QLabel(title, parent)
    title_label.setObjectName("CardTitle")
    value = QLabel("—", parent)
    value.setObjectName("CardValue")
    hint = QLabel("", parent)
    hint.setObjectName("CardHint")
    hint.setWordWrap(True)
    hint.setAlignment(Qt.AlignmentFlag.AlignTop)
    inner = QVBoxLayout()
    inner.setContentsMargins(12, 6, 12, 10)
    inner.setSpacing(2)
    inner.addWidget(title_label)
    inner.addWidget(value)
    inner.addWidget(hint, stretch=1)
    group = QGroupBox(parent)
    group.setObjectName("MetricCard")
    group.setLayout(inner)
    group.setFixedHeight(_CARD_HEIGHT)
    group.setAccessibleName(title)
    return group, value, hint


def _set_card(value: QLabel, hint: QLabel, text: str, summary: str, detail: str = "") -> None:
    """Fill one card.

    ``summary`` is the one line the card shows; ``detail`` is the longer
    version, which stays available as a tooltip instead of overflowing the
    card and pushing the row out of alignment.
    """
    value.setText(text)
    hint.setText(summary)
    tooltip = detail or summary
    value.setToolTip(tooltip)
    hint.setToolTip(tooltip)


class DashboardView(QWidget):
    """Shows the active device, today's attendance and sync state."""

    def __init__(
        self,
        context: ApplicationContext,
        service: DeviceService,
        parent: QWidget | None = None,
        *,
        role: Role | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._service = service
        self._sync = context.sync
        #: The logged-in role. Viewers cannot sync; ``None`` keeps the
        #: legacy behaviour for tests.
        self._role = normalise_role(role) if role is not None else None
        self._profile: DeviceProfile | None = None

        self._device_summary = QLabel("Loading…", self)
        self._device_summary.setWordWrap(True)

        self._clock_card, self._clock_value, self._clock_hint = _stat_card("Clock", self)
        self._presence_card, self._presence_value, self._presence_hint = _stat_card("In now", self)
        self._today_card, self._today_value, self._today_hint = _stat_card("Today", self)
        self._sync_card, self._sync_value, self._sync_hint = _stat_card("Sync", self)
        cards = QHBoxLayout()
        for card in (
            self._clock_card,
            self._presence_card,
            self._today_card,
            self._sync_card,
        ):
            cards.addWidget(card, stretch=1)

        # The name column takes the spare width; the time and direction
        # columns are a fixed shape, so they size to their content.
        self._recent_table = build_table(
            ["Time", "Employee", "Direction"], self, sortable=False, stretch_columns=(1,)
        )
        self._recent_table.setMinimumHeight(150)
        self._recent_table.setMaximumHeight(230)
        self._recent_table.setAccessibleName("Recent punches")
        recent_layout = QVBoxLayout()
        recent_layout.addWidget(self._recent_table)
        self._recent_group = QGroupBox("Recent punches", self)
        self._recent_group.setLayout(recent_layout)

        self._chart = TrendChart(self)
        chart_layout = QVBoxLayout()
        chart_layout.addWidget(self._chart)
        self._chart_group = QGroupBox("Attendance this week", self)
        self._chart_group.setLayout(chart_layout)

        middle = QHBoxLayout()
        middle.addWidget(self._chart_group, stretch=3)
        middle.addWidget(self._recent_group, stretch=2)

        self._exceptions = QLabel("", self)
        self._exceptions.setWordWrap(True)

        self._device_table = build_table(["Item", "Value"], self, sortable=False)
        _make_reference_table(self._device_table)
        device_layout = QVBoxLayout()
        device_layout.addWidget(self._device_summary)
        device_layout.addWidget(self._device_table)
        self._device_group = QGroupBox("Device", self)
        self._device_group.setLayout(device_layout)

        self._app_table = build_table(["Item", "Value"], self, sortable=False)
        _make_reference_table(self._app_table)
        app_layout = QVBoxLayout()
        app_layout.addWidget(self._app_table)
        app_group = QGroupBox("Application", self)
        app_group.setLayout(app_layout)

        self._connect_button = QPushButton("Check device connection", self)
        self._connect_button.clicked.connect(self.check_connection)
        self._connect_button.setToolTip("Contact the clock and confirm it answers.")
        self._sync_button = primary_button("Sync now", self)
        self._sync_button.clicked.connect(self.sync_now)
        self._sync_button.setToolTip("Pull the device history into local storage.")
        if not role_allows(self._role, Permission.SYNC_ATTENDANCE):
            self._sync_button.setEnabled(False)
            self._sync_button.setToolTip(
                "Your role is read-only. Only office staff and administrators may sync."
            )
        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.clicked.connect(self.refresh)
        self._refresh_button.setShortcut("F5")

        # Actions sit to the right of the page heading, where a business
        # application puts them, rather than in a bar above the figures.
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._refresh_button)
        buttons.addWidget(self._connect_button)
        buttons.addWidget(self._sync_button)

        # The dashboard has deliberately useful detail below the headline
        # cards.  Keep it available on a 720px laptop window rather than
        # crushing the tables into unreadable slivers.
        content = QWidget(self)
        layout = QVBoxLayout(content)
        heading = QHBoxLayout()
        heading.addWidget(
            page_header("Dashboard", "Today at a glance: attendance, sync state and exceptions."),
            stretch=1,
        )
        heading.addLayout(buttons)
        layout.addLayout(heading)
        layout.addLayout(cards)
        layout.addLayout(middle)
        layout.addWidget(self._exceptions)
        layout.addWidget(self._device_group, stretch=1)
        layout.addWidget(app_group, stretch=1)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)
        self.setLayout(root)

        self.refresh()

    def refresh(self) -> None:
        """Reload the active profile and application status off the UI thread."""
        self._refresh_button.setEnabled(False)
        set_status(self._device_summary, "Loading…", "loading")
        run_off_thread(
            self._collect,
            on_success=self._on_loaded,
            on_failure=self._on_failure,
        )

    def _collect(self) -> _DashboardData:
        service = self._service
        profile = service.first_enabled_profile()
        status = self._context.status()
        today = date.today()  # noqa: DTZ011 - device-local business day, not a timestamp

        if profile is None or profile.device_id is None:
            return _DashboardData(
                profile=profile,
                status=status,
                clock_state="No device",
                clock_hint="Add one in Device settings.",
                clock_detail="",
                in_now=0,
                out_now=0,
                punches_today=0,
                sync_state="—",
                sync_hint="Nothing stored yet.",
                sync_detail="",
                exceptions="",
                recent_rows=[],
                trend_labels=[],
                trend_in=[],
                trend_out=[],
            )

        try:
            device_status = service.status(profile)
            clock_state = "Enabled" if profile.enabled else "Disabled"
            clock_hint = f"Last seen {_short_when(profile.last_seen_at)}."
            clock_detail = device_status.describe()
        except Exception:
            clock_state = "Unknown"
            clock_hint = "Could not read local device state."
            clock_detail = ""
        try:
            sync_summary = self._sync.summary(profile)
            history = self._sync.history(profile, limit=1)
            last_outcome = history[0].outcome if history else None
            last_error = (history[0].error or "") if history else ""
        except Exception:
            sync_summary = None
            last_outcome = None
            last_error = ""
        try:
            recent = self._sync.list_recent(limit=500)
        except Exception:
            recent = []

        todays = [e for e in recent if e.occurred_at.date() == today]
        latest_by_user: dict[str, int] = {}
        for event in todays:
            if event.user_id not in latest_by_user:
                latest_by_user[event.user_id] = event.punch
        in_now = sum(1 for punch in latest_by_user.values() if punch == PunchDirection.IN)
        out_now = sum(1 for punch in latest_by_user.values() if punch == PunchDirection.OUT)
        unknown = [e for e in todays if e.punch not in (0, 1)]

        sync_detail = ""
        if sync_summary is None:
            sync_state, sync_hint = "Unknown", "Could not read sync state."
        elif sync_summary.stored_events == 0 and sync_summary.last_success_at is None:
            sync_state, sync_hint = "Never synced", "Press “Sync now” to store history."
        elif last_outcome == "failed":
            sync_state = "Offline"
            sync_hint = "Last attempt failed."
            sync_detail = f"Last attempt failed: {last_error}" if last_error else ""
        else:
            sync_state = f"{sync_summary.stored_events} stored"
            sync_hint = f"Last sync {_short_when(sync_summary.last_success_at)}."

        exceptions = ""
        if unknown:
            exceptions += (
                f"{len(unknown)} punch(es) today carry an unrecognised punch value "
                "(see Attendance). "
            )
        if last_outcome == "failed" and last_error:
            exceptions += f"Last sync failed: {last_error}"
        exceptions = exceptions.strip()

        # A week of daily IN/OUT totals: the shape of the week is the thing
        # an owner reads first, and it comes from punches already stored.
        trend_labels: list[str] = []
        trend_in: list[int] = []
        trend_out: list[int] = []
        for offset in range(_TREND_DAYS - 1, -1, -1):
            day = today - timedelta(days=offset)
            day_events = [event for event in recent if event.occurred_at.date() == day]
            trend_labels.append(day.strftime("%a"))
            trend_in.append(sum(1 for e in day_events if e.punch == PunchDirection.IN))
            trend_out.append(sum(1 for e in day_events if e.punch == PunchDirection.OUT))

        recent_rows = [
            [
                e.occurred_at.strftime("%H:%M"),
                e.display_name,
                e.direction_label,
            ]
            for e in todays[:_RECENT_LIMIT]
        ]
        return _DashboardData(
            profile=profile,
            status=status,
            clock_state=clock_state,
            clock_hint=clock_hint,
            clock_detail=clock_detail,
            in_now=in_now,
            out_now=out_now,
            punches_today=len(todays),
            sync_state=sync_state,
            sync_hint=sync_hint,
            sync_detail=sync_detail,
            exceptions=exceptions,
            recent_rows=recent_rows,
            trend_labels=trend_labels,
            trend_in=trend_in,
            trend_out=trend_out,
        )

    def _on_loaded(self, payload: Any) -> None:
        self._refresh_button.setEnabled(True)
        if isinstance(payload, tuple):  # pragma: no cover - legacy shape, defensive
            profile, status = payload
            self._profile = profile
            fill_table(self._app_table, [list(row) for row in status.as_rows()])
            return
        if not isinstance(payload, _DashboardData):  # pragma: no cover - defensive
            return
        profile, status = payload.profile, payload.status
        self._profile = profile

        fill_table(self._app_table, [list(row) for row in status.as_rows()])
        _set_card(
            self._clock_value,
            self._clock_hint,
            payload.clock_state,
            payload.clock_hint,
            payload.clock_detail,
        )
        _set_card(
            self._presence_value,
            self._presence_hint,
            f"{payload.in_now} in / {payload.out_now} out",
            "Latest punch per person today.",
        )
        _set_card(
            self._today_value,
            self._today_hint,
            str(payload.punches_today),
            "Punches stored for today.",
        )
        _set_card(
            self._sync_value,
            self._sync_hint,
            payload.sync_state,
            payload.sync_hint,
            payload.sync_detail,
        )
        fill_table(
            self._recent_table,
            payload.recent_rows,
            empty_message="No punches stored for today yet. Press “Sync now” to read the clock.",
        )
        _colour_directions(self._recent_table, payload.recent_rows)
        _badge_people(self._recent_table, payload.recent_rows)
        self._chart.set_data(
            payload.trend_labels,
            [
                ChartSeries("IN", payload.trend_in, "badge_in"),
                ChartSeries("OUT", payload.trend_out, "badge_out"),
            ]
            if any(payload.trend_in) or any(payload.trend_out)
            else [],
            empty_text="No punches stored for the past week yet.",
        )
        if payload.exceptions:
            set_status(self._exceptions, f"Needs attention: {payload.exceptions}", "warning")
        else:
            set_status(self._exceptions, "", "info")

        if profile is None:
            set_status(
                self._device_summary,
                "No device is configured yet. Add one in Device settings.",
                "warning",
            )
            self._connect_button.setEnabled(False)
            fill_table(
                self._device_table,
                [],
                empty_message="No device configured. Open Device settings to add one.",
            )
            return

        self._connect_button.setEnabled(profile.is_configured)
        set_status(
            self._device_summary,
            f"{profile.name} at {profile.endpoint}"
            if profile.is_configured
            else f"{profile.name} has no address configured.",
            "info",
        )
        fill_table(
            self._device_table,
            [
                ["Name", profile.name],
                ["Address", profile.endpoint],
                ["Model", profile.model or "Not yet read from device"],
                ["Platform", profile.platform or "Not yet read from device"],
                ["Firmware", profile.firmware_version or "Not yet read from device"],
                ["Serial number", profile.serial_number or "Not yet read from device"],
                ["Communication password", "Set" if profile.has_communication_password else "None"],
                ["Auto reconnect", "On" if profile.auto_reconnect else "Off"],
                ["Sync interval", f"{profile.sync_interval_seconds} s"],
                ["Enabled", "Yes" if profile.enabled else "No"],
            ],
        )

    def check_connection(self) -> None:
        profile = self._profile
        if profile is None:
            return
        self._connect_button.setEnabled(False)
        set_status(self._device_summary, f"Connecting to {profile.endpoint}…", "loading")
        run_off_thread(
            lambda: self._service.test_connection(profile),
            on_success=self._on_connection_checked,
            on_failure=self._on_failure,
        )

    def sync_now(self) -> None:
        """Pull the device history into local storage, then refresh."""
        profile = self._profile
        if profile is None or profile.device_id is None:
            set_status(
                self._device_summary,
                "No device is configured yet. Add one in Device settings.",
                "warning",
            )
            return
        self._sync_button.setEnabled(False)
        set_status(self._device_summary, "Syncing… reading the device history.", "loading")
        run_off_thread(
            lambda: self._sync.manual_sync(profile, requester_role=self._role),
            on_success=self._on_synced,
            on_failure=self._on_failure,
        )

    def _on_connection_checked(self, result: Any) -> None:
        self._connect_button.setEnabled(True)
        if not isinstance(result, ConnectionTestResult):  # pragma: no cover - defensive
            return

        set_status(
            self._device_summary,
            result.summary,
            "success" if result.ok else "error",
        )
        if result.info is not None:
            fill_table(self._device_table, [list(row) for row in result.info.as_rows()])

    def _on_synced(self, result: Any) -> None:
        self._sync_button.setEnabled(role_allows(self._role, Permission.SYNC_ATTENDANCE))
        ok = bool(getattr(result, "ok", False))
        summary = str(getattr(result, "summary", "Sync finished."))
        set_status(self._device_summary, summary, "success" if ok else "error")
        notify(self, summary, kind="success" if ok else "error")
        self.refresh()

    def _on_failure(self, message: str) -> None:
        notify(self, message, kind="error")
        self._refresh_button.setEnabled(True)
        self._connect_button.setEnabled(True)
        self._sync_button.setEnabled(role_allows(self._role, Permission.SYNC_ATTENDANCE))
        set_status(self._device_summary, message, "error")


def _make_reference_table(table: QTableWidget) -> None:
    """Turn a label/value table into plain reference text.

    These tables show settings, not records: nothing can be opened from a
    row, so a selection highlight only ever looks like an accident.
    """
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setAlternatingRowColors(True)


def _colour_directions(table: QTableWidget, rows: list[list[str]]) -> None:
    """Tint the direction column so IN and OUT separate at a glance."""
    for index, row in enumerate(rows):
        if len(row) < 3:  # pragma: no cover - defensive
            continue
        token = "badge_in" if row[2].upper().startswith("IN") else "badge_out"
        tint_cell(table, index, 2, token)


def _badge_people(table: QTableWidget, rows: list[list[str]]) -> None:
    """Put an initials badge next to each person in the recent list."""
    palette = current_palette()
    for index, row in enumerate(rows):
        if len(row) < 2:  # pragma: no cover - defensive
            continue
        item = table.item(index, 1)
        if item is None:  # pragma: no cover - defensive
            continue
        item.setIcon(avatar_pixmap(row[1], palette.accent_soft, palette.text))
