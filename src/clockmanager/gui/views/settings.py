"""Settings: one place for everything that configures the application.

Five sections, each on its own tab:

``General``
    Appearance, and where this installation keeps its data.
``Device``
    The stored connection profile, device state and discovery — the existing
    :class:`DeviceSettingsView`, hosted here rather than in the navigation.
``Payroll``
    Pay schedules and timesheet rules. Administrator-only: office staff
    maintain employees, but overtime thresholds decide what people are paid.
``Security``
    Application accounts, and a plain statement of what this build may write.
``Developer``
    Diagnostics and database metadata.

A section a role may not use is **not added at all**, so office staff have no
Device, Security or Developer tab to find (``SECURITY.md``). The hub owns no
business rules: it hosts existing views and calls the same services they do.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.domain.payroll import PayScheduleType
from clockmanager.gui.theme import ThemeName, current_theme, set_theme
from clockmanager.gui.views.common import (
    build_table,
    confirm,
    fill_table,
    muted_label,
    notify,
    page_header,
    primary_button,
    role_allows,
    run_off_thread,
    set_status,
)
from clockmanager.services.application import ApplicationContext
from clockmanager.services.timesheets import PayScheduleProfile

__all__ = ["SettingsView", "sections_for"]

_logger = get_logger(__name__)

#: Section labels in tab order. Kept as data so the role filter and the tests
#: agree on one list.
GENERAL, DEVICE, PAYROLL, SECURITY, DEVELOPER = (
    "General",
    "Device",
    "Payroll",
    "Security",
    "Developer",
)

_SCHEDULE_TYPES: tuple[tuple[str, PayScheduleType], ...] = (
    ("Weekly", PayScheduleType.WEEKLY),
    ("Fortnightly", PayScheduleType.BIWEEKLY),
    ("Twice monthly", PayScheduleType.SEMIMONTHLY),
    ("Monthly", PayScheduleType.MONTHLY),
)


def sections_for(role: Role | None) -> tuple[str, ...]:
    """Return the settings sections ``role`` may open.

    ``None`` is the pre-login/test path and sees everything, matching the
    rest of the GUI. Everyone gets General; the restricted sections follow
    the same permissions that gate the views they host.
    """
    if role is None:
        return (GENERAL, DEVICE, PAYROLL, SECURITY, DEVELOPER)
    sections = [GENERAL]
    if role_allows(role, Permission.MANAGE_DEVICE_SETTINGS):
        sections.append(DEVICE)
    sections.append(PAYROLL)  # read-only for roles without the permission
    if role_allows(role, Permission.MANAGE_ACCOUNTS):
        sections.append(SECURITY)
    if role_allows(role, Permission.VIEW_DIAGNOSTICS):
        sections.append(DEVELOPER)
    return tuple(sections)


class SettingsView(QWidget):
    """Hosts the configuration sections this role may use."""

    def __init__(
        self,
        context: ApplicationContext,
        *,
        device_settings: QWidget,
        diagnostics: QWidget,
        accounts: QWidget | None = None,
        parent: QWidget | None = None,
        role: Role | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._timesheets = context.timesheets
        self._role = normalise_role(role) if role is not None else None
        self._schedules: list[PayScheduleProfile] = []

        self._tabs = QTabWidget(self)
        self._tabs.setAccessibleName("Settings sections")
        sections = sections_for(self._role)

        self._tabs.addTab(self._build_general(), GENERAL)
        if DEVICE in sections:
            self._tabs.addTab(device_settings, DEVICE)
        if PAYROLL in sections:
            self._tabs.addTab(self._build_payroll(), PAYROLL)
        if SECURITY in sections and accounts is not None:
            self._tabs.addTab(self._build_security(accounts), SECURITY)
        if DEVELOPER in sections:
            self._tabs.addTab(self._build_developer(diagnostics), DEVELOPER)

        layout = QVBoxLayout()
        layout.addWidget(
            page_header(
                "Settings",
                "How this installation looks, how it reaches the clock, "
                "how pay periods are worked out, and who may sign in.",
            )
        )
        layout.addWidget(self._tabs, stretch=1)
        self.setLayout(layout)

    @property
    def section_labels(self) -> tuple[str, ...]:
        """The sections actually shown, in tab order."""
        return tuple(self._tabs.tabText(index) for index in range(self._tabs.count()))

    def show_section(self, label: str) -> bool:
        """Open one section by name. Returns whether it exists for this role."""
        for index in range(self._tabs.count()):
            if self._tabs.tabText(index) == label:
                self._tabs.setCurrentIndex(index)
                return True
        return False

    def load(self) -> None:
        """Refresh the sections that read stored state."""
        if PAYROLL in self.section_labels:
            self._load_schedules()

    # -- general --------------------------------------------------------------

    def _build_general(self) -> QWidget:
        """Appearance and where this installation keeps its files."""
        page = QWidget(self)

        self._light_radio = QRadioButton("Light", page)
        self._dark_radio = QRadioButton("Dark", page)
        (self._dark_radio if current_theme() == "dark" else self._light_radio).setChecked(True)
        self._light_radio.toggled.connect(self._on_theme_toggled)
        appearance_row = QHBoxLayout()
        appearance_row.addWidget(self._light_radio)
        appearance_row.addWidget(self._dark_radio)
        appearance_row.addStretch(1)
        appearance_inner = QVBoxLayout()
        appearance_inner.addLayout(appearance_row)
        appearance_inner.addWidget(
            muted_label("The choice is remembered on this computer for the next start.", page)
        )
        appearance = QGroupBox("Appearance", page)
        appearance.setLayout(appearance_inner)
        appearance.setMaximumWidth(640)

        self._about_table = build_table(["Item", "Value"], page, sortable=False)
        self._about_table.setMaximumWidth(640)
        status = self._context.status()
        fill_table(self._about_table, [list(row) for row in status.as_rows()])
        about_inner = QVBoxLayout()
        about_inner.addWidget(self._about_table)
        about = QGroupBox("This installation", page)
        about.setLayout(about_inner)
        about.setMaximumWidth(640)

        layout = QVBoxLayout(page)
        layout.addWidget(appearance)
        layout.addWidget(about, stretch=1)
        layout.addStretch(1)
        return page

    def _on_theme_toggled(self, light_selected: bool) -> None:
        """Apply the appearance immediately: a preview is the whole point."""
        name: ThemeName = "light" if light_selected else "dark"
        app = QApplication.instance()
        if isinstance(app, QApplication):
            set_theme(app, name)
        window = self.window()
        repaint = getattr(window, "repaint_theme_artwork", None)
        if callable(repaint):
            repaint()

    # -- payroll --------------------------------------------------------------

    def _build_payroll(self) -> QWidget:
        """Pay schedules: what is active now, and how to change it."""
        page = QWidget(self)
        may_manage = role_allows(self._role, Permission.MANAGE_PAYROLL)

        self._schedule_table = build_table(
            ["Name", "Type", "Anchor", "Time zone", "Daily OT", "Weekly OT", "Active"],
            page,
            stretch_columns=(0,),
        )
        self._schedule_table.setMaximumHeight(190)
        self._schedule_table.itemSelectionChanged.connect(self._update_payroll_buttons)

        self._activate_button = primary_button("Make active", page)
        self._activate_button.clicked.connect(self._activate_selected)
        self._reload_button = QLabel("", page)  # placeholder keeps the row shape

        stored_row = QHBoxLayout()
        stored_row.addStretch(1)
        stored_row.addWidget(self._activate_button)

        stored_inner = QVBoxLayout()
        stored_inner.addWidget(
            muted_label(
                "One schedule is active at a time. It decides the pay period every "
                "timesheet and pay-period report is built against.",
                page,
            )
        )
        stored_inner.addWidget(self._schedule_table)
        stored_inner.addLayout(stored_row)
        stored = QGroupBox("Pay schedules", page)
        stored.setLayout(stored_inner)

        # -- new schedule form
        self._name = QLineEdit(page)
        self._name.setPlaceholderText("e.g. Fortnightly, Monday start")
        self._type = QComboBox(page)
        for label, kind in _SCHEDULE_TYPES:
            self._type.addItem(label, kind.value)
        self._anchor = QDateEdit(page)
        self._anchor.setCalendarPopup(True)
        self._anchor.setDisplayFormat("yyyy-MM-dd")
        self._anchor.setDate(QDate.currentDate())
        self._anchor.setToolTip("The day a pay period starts. Every later period counts from here.")
        self._timezone = QLineEdit(page)
        self._timezone.setText("UTC")
        self._timezone.setToolTip("An IANA name, such as Australia/Melbourne.")
        self._cutoff = QSpinBox(page)
        self._cutoff.setRange(0, 23)
        self._cutoff.setSuffix(":00")
        self._cutoff.setToolTip("The hour a business day starts, for shifts crossing midnight.")
        self._duplicate = QSpinBox(page)
        self._duplicate.setRange(0, 3600)
        self._duplicate.setValue(60)
        self._duplicate.setSuffix(" s")
        self._duplicate.setToolTip("Two punches closer together than this count once.")
        self._max_shift = QDoubleSpinBox(page)
        self._max_shift.setRange(1.0, 24.0)
        self._max_shift.setValue(16.0)
        self._max_shift.setSuffix(" h")
        self._max_shift.setToolTip("A shift longer than this is flagged rather than paid.")
        self._daily_ot = QDoubleSpinBox(page)
        self._daily_ot.setRange(0.0, 24.0)
        self._daily_ot.setSpecialValueText("None")
        self._daily_ot.setSuffix(" h")
        self._weekly_ot = QDoubleSpinBox(page)
        self._weekly_ot.setRange(0.0, 168.0)
        self._weekly_ot.setSpecialValueText("None")
        self._weekly_ot.setSuffix(" h")
        self._decimal = QCheckBox("Show hours as decimals (7.5 rather than 7:30)", page)
        self._activate_new = QCheckBox("Make this the active schedule", page)
        self._activate_new.setChecked(True)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.addRow("Name", self._name)
        form.addRow("Type", self._type)
        form.addRow("First period starts", self._anchor)
        form.addRow("Time zone", self._timezone)
        form.addRow("Day starts at", self._cutoff)
        form.addRow("Ignore repeats within", self._duplicate)
        form.addRow("Longest shift", self._max_shift)
        form.addRow("Daily overtime after", self._daily_ot)
        form.addRow("Weekly overtime after", self._weekly_ot)
        form.addRow("", self._decimal)
        form.addRow("", self._activate_new)

        self._create_button = primary_button("Create schedule", page)
        self._create_button.clicked.connect(self._create_schedule)
        create_row = QHBoxLayout()
        create_row.addStretch(1)
        create_row.addWidget(self._create_button)

        create_inner = QVBoxLayout()
        create_inner.addLayout(form)
        create_inner.addLayout(create_row)
        create = QGroupBox("Add a pay schedule", page)
        create.setLayout(create_inner)
        create.setMaximumWidth(640)

        self._payroll_status = QLabel("", page)
        self._payroll_status.setWordWrap(True)

        layout = QVBoxLayout(page)
        layout.addWidget(stored)
        layout.addWidget(self._payroll_status)
        layout.addWidget(create, stretch=1)

        if not may_manage:
            # Read-only for anyone without the payroll permission: the figures
            # stay visible because timesheets are read against them.
            create.setEnabled(False)
            self._activate_button.setEnabled(False)
            label = self._role.label if self._role is not None else "this role"
            layout.addWidget(
                muted_label(
                    f"Your role ({label}) can see the pay rules but not change them. "
                    "Only administrators may add or activate a pay schedule.",
                    page,
                )
            )
        self._load_schedules()
        return page

    def _load_schedules(self) -> None:
        set_status(self._payroll_status, "Loading pay schedules…", "loading")
        run_off_thread(
            self._timesheets.list_schedules,
            on_success=self._on_schedules,
            on_failure=self._on_payroll_failure,
        )

    def _on_schedules(self, schedules: Any) -> None:
        if not isinstance(schedules, list):  # pragma: no cover - defensive
            return
        self._schedules = schedules
        fill_table(
            self._schedule_table,
            [
                [
                    profile.name,
                    _type_label(profile.schedule_type),
                    profile.anchor_date.isoformat(),
                    profile.timezone,
                    _hours_label(profile.daily_overtime_hours),
                    _hours_label(profile.weekly_overtime_hours),
                    "Active" if profile.is_active else "",
                ]
                for profile in schedules
            ],
            empty_message="No pay schedule yet. One is created the first time a timesheet is built.",
        )
        active = next((profile for profile in schedules if profile.is_active), None)
        if active is None:
            set_status(self._payroll_status, "No schedule is active yet.", "warning")
        else:
            set_status(
                self._payroll_status,
                f"Active: {active.name} — {_type_label(active.schedule_type)}, "
                f"periods counted from {active.anchor_date.isoformat()} ({active.timezone}).",
                "info",
            )
        self._update_payroll_buttons()

    def _selected_schedule(self) -> PayScheduleProfile | None:
        rows = {index.row() for index in self._schedule_table.selectionModel().selectedRows()}
        if len(rows) != 1:
            return None
        item = self._schedule_table.item(rows.pop(), 0)
        if item is None:
            return None
        return next((s for s in self._schedules if s.name == item.text()), None)

    def _update_payroll_buttons(self) -> None:
        selected = self._selected_schedule()
        may_manage = role_allows(self._role, Permission.MANAGE_PAYROLL)
        self._activate_button.setEnabled(
            may_manage and selected is not None and not selected.is_active
        )

    def _activate_selected(self) -> None:
        selected = self._selected_schedule()
        if selected is None:
            return
        if not confirm(
            self,
            "Change the active pay schedule",
            f"Make “{selected.name}” the active pay schedule?",
            detail=(
                "Timesheets and pay-period reports built from now on use this "
                "schedule's periods and rules. Stored punches are not changed."
            ),
            confirm_label="Make active",
        ):
            set_status(self._payroll_status, "Left the active schedule unchanged.", "info")
            return
        schedule_id = selected.schedule_id
        set_status(self._payroll_status, "Activating…", "loading")
        run_off_thread(
            lambda: self._timesheets.activate_schedule(schedule_id, requester_role=self._role),
            on_success=self._on_activated,
            on_failure=self._on_payroll_failure,
        )

    def _on_activated(self, profile: Any) -> None:
        name = getattr(profile, "name", "the schedule")
        notify(self, f"“{name}” is now the active pay schedule.")
        self._load_schedules()

    def _create_schedule(self) -> None:
        name = self._name.text().strip()
        if not name:
            set_status(self._payroll_status, "Give the pay schedule a name.", "error")
            self._name.setFocus()
            return
        anchor = self._anchor.date()
        schedule_type = PayScheduleType(str(self._type.currentData()))
        anchor_date = date(anchor.year(), anchor.month(), anchor.day())
        timezone = self._timezone.text().strip() or "UTC"
        cutoff = self._cutoff.value()
        duplicate = self._duplicate.value()
        max_shift = self._max_shift.value()
        decimal = self._decimal.isChecked()
        # The spin boxes use their minimum as "no overtime rule".
        daily_ot = self._daily_ot.value() or None
        weekly_ot = self._weekly_ot.value() or None
        activate = self._activate_new.isChecked()

        set_status(self._payroll_status, "Saving the pay schedule…", "loading")
        run_off_thread(
            lambda: self._timesheets.create_schedule(
                requester_role=self._role,
                name=name,
                schedule_type=schedule_type,
                anchor_date=anchor_date,
                timezone=timezone,
                day_cutoff_hour=cutoff,
                duplicate_interval_seconds=duplicate,
                max_shift_hours=max_shift,
                display_decimal=decimal,
                daily_overtime_hours=daily_ot,
                weekly_overtime_hours=weekly_ot,
                activate=activate,
            ),
            on_success=self._on_created,
            on_failure=self._on_payroll_failure,
        )

    def _on_created(self, profile: Any) -> None:
        name = getattr(profile, "name", "Pay schedule")
        self._name.clear()
        notify(self, f"Saved pay schedule “{name}”.")
        self._load_schedules()

    def _on_payroll_failure(self, message: str) -> None:
        set_status(self._payroll_status, message, "error")
        notify(self, message, kind="error")

    # -- security -------------------------------------------------------------

    def _build_security(self, accounts: QWidget) -> QWidget:
        """Accounts, plus a plain statement of what this build may write."""
        page = QWidget(self)
        config = self._context.config

        posture = build_table(["Item", "Value"], page, sortable=False)
        posture.setMaximumHeight(150)
        fill_table(
            posture,
            [
                [
                    "Device user writing",
                    "Enabled (unverified on real hardware)"
                    if config.enable_device_writes
                    else "Disabled — this build only reads from the clock",
                ],
                [
                    "PIN / credential writing",
                    "Enabled (unverified)" if config.enable_credential_writes else "Disabled",
                ],
                ["Developer mode", "On" if config.developer_mode else "Off"],
                ["Database", str(config.paths.database_file)],
            ],
        )
        posture_inner = QVBoxLayout()
        posture_inner.addWidget(posture)
        posture_inner.addWidget(
            muted_label(
                "Write modes are set in the configuration file, not here, so they "
                "cannot be switched on by accident from the application.",
                page,
            )
        )
        posture_group = QGroupBox("What this build may change", page)
        posture_group.setLayout(posture_inner)

        layout = QVBoxLayout(page)
        layout.addWidget(accounts, stretch=1)
        layout.addWidget(posture_group)
        return page

    # -- developer ------------------------------------------------------------

    def _build_developer(self, diagnostics: QWidget) -> QWidget:
        """Diagnostics and database metadata, for administrators only."""
        page = QWidget(self)
        metadata_button = primary_button("Show database metadata", page)
        metadata_button.clicked.connect(self._show_schema_info)
        metadata_row = QHBoxLayout()
        metadata_row.addWidget(metadata_button)
        metadata_row.addStretch(1)

        layout = QVBoxLayout(page)
        layout.addWidget(diagnostics, stretch=1)
        layout.addLayout(metadata_row)
        layout.addWidget(
            muted_label(
                "Developer tools are administrator-only and never shown to office "
                "staff. Exports are sanitised and cannot disclose a credential.",
                page,
            )
        )
        return page

    def _show_schema_info(self) -> None:
        info = self._context.schema_info()
        body = "\n".join(f"{key}: {value}" for key, value in sorted(info.items()))
        QMessageBox.information(self, "Database metadata", body or "No metadata recorded.")


def _type_label(schedule_type: str) -> str:
    """Business wording for a stored schedule type."""
    for label, kind in _SCHEDULE_TYPES:
        if kind.value == schedule_type:
            return label
    return schedule_type


def _hours_label(hours: float | None) -> str:
    return "—" if hours is None else f"{hours:g} h"
