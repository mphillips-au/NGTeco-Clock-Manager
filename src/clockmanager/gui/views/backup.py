"""Backup and offline resilience view (PHASE 09).

Creates, lists, previews and restores application backups, and shows what
keeps working while the clock is unreachable. Restoring replaces local data,
so it needs both a preview and an explicit confirmation, and it always keeps
an automatic pre-restore safety backup.

The view holds no business rules: every operation goes through
:mod:`clockmanager.services.backup`, always off the UI thread.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    role_allows,
    run_off_thread,
    section_label,
)
from clockmanager.services.application import ApplicationContext
from clockmanager.services.backup import BackupSummary, OfflineReport, RestorePreview

__all__ = ["BackupView", "RestoreConfirmDialog"]


class RestoreConfirmDialog(QDialog):
    """Explicit confirmation for a restore, showing exactly what it will do."""

    def __init__(self, preview: RestorePreview, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm restore")
        self._understood = QCheckBox(
            "I understand this replaces the local database and configuration.", self
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok, self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Restore")
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setEnabled(False)
        self._understood.toggled.connect(
            lambda checked: ok_button.setEnabled(checked) if ok_button is not None else None
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        detail = QLabel(preview.describe(), self)
        detail.setWordWrap(True)
        safety = QLabel(
            "A pre-restore safety backup is taken first, and the restore itself "
            "is written to the audit log.",
            self,
        )
        safety.setWordWrap(True)
        layout.addWidget(detail)
        layout.addWidget(safety)
        layout.addWidget(self._understood)
        layout.addWidget(buttons)
        self.setLayout(layout)


class BackupView(QWidget):
    """Backup, restore and offline-status controls."""

    _HEADERS = ("Backup", "Label", "Created", "Schema", "Size (bytes)")

    def __init__(
        self,
        context: ApplicationContext,
        parent: QWidget | None = None,
        *,
        role: Role | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._backups = context.backups
        #: Only administrators may change local state through backups; ``None``
        #: keeps the legacy behaviour for tests.
        self._role = normalise_role(role) if role is not None else None
        self._listed: list[BackupSummary] = []
        self._preview: RestorePreview | None = None

        self._offline_label = QLabel("Loading offline status…", self)
        self._offline_label.setWordWrap(True)
        self._refresh_offline_button = QPushButton("Refresh offline status", self)
        self._refresh_offline_button.clicked.connect(self._load_offline)

        self._label = QLineEdit(self)
        self._label.setPlaceholderText("nightly")
        self._label.setText("manual")
        self._create_button = QPushButton("Create backup now", self)
        self._create_button.clicked.connect(self._create)

        self._table = build_table(self._HEADERS, self)
        self._table.itemSelectionChanged.connect(self._on_selected)

        self._preview_label = QLabel("Select a backup to preview it.", self)
        self._preview_label.setWordWrap(True)
        self._restore_button = QPushButton("Restore selected backup…", self)
        self._restore_button.clicked.connect(self._restore)
        self._restore_button.setEnabled(False)
        self._status = QLabel("", self)
        self._status.setWordWrap(True)

        may_manage = role_allows(self._role, Permission.MANAGE_DEVICE_SETTINGS)
        self.layout_widgets_locked: QLabel | None = None
        if not may_manage:
            locked = QLabel(
                "Backups hold the full database copy, including device connection "
                "secrets. Only administrators may create or restore them.",
                self,
            )
            locked.setWordWrap(True)
            self._create_button.setEnabled(False)
            self._restore_button.setEnabled(False)
            self.layout_widgets_locked = locked

        top = QHBoxLayout()
        top.addWidget(QLabel("Label:", self))
        top.addWidget(self._label, stretch=1)
        top.addWidget(self._create_button)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Offline status", self))
        layout.addWidget(self._offline_label)
        layout.addWidget(self._refresh_offline_button)
        layout.addWidget(section_label("Backups", self))
        layout.addLayout(top)
        layout.addWidget(self._table)
        layout.addWidget(self._preview_label)
        layout.addWidget(self._restore_button)
        layout.addWidget(self._status)
        if self.layout_widgets_locked is not None:
            layout.addWidget(self.layout_widgets_locked)
        layout.addStretch(1)
        self.setLayout(layout)

        self.load()

    # -- data -----------------------------------------------------------------

    def load(self) -> None:
        """Reload the offline report and the backup list."""
        self._load_offline()
        self._load_list()

    def _load_offline(self) -> None:
        run_off_thread(
            self._backups.offline_report,
            on_success=self._on_offline,
            on_failure=self._on_failure,
        )

    def _on_offline(self, report: Any) -> None:
        if not isinstance(report, OfflineReport):  # pragma: no cover - defensive
            return
        self._offline_label.setText(report.describe())

    def _load_list(self) -> None:
        run_off_thread(
            self._backups.list_backups,
            on_success=self._on_listed,
            on_failure=self._on_failure,
        )

    def _on_listed(self, listed: Any) -> None:
        if not isinstance(listed, list):  # pragma: no cover - defensive
            return
        self._listed = [item for item in listed if isinstance(item, BackupSummary)]
        fill_table(
            self._table,
            [
                [
                    item.path.name,
                    item.label,
                    item.created_at,
                    "" if item.schema_version is None else str(item.schema_version),
                    str(item.size_bytes),
                ]
                for item in self._listed
            ],
        )
        self._preview = None
        self._restore_button.setEnabled(False)
        self._preview_label.setText("Select a backup to preview it.")

    # -- actions --------------------------------------------------------------

    def _selected(self) -> BackupSummary | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        return self._listed[row] if 0 <= row < len(self._listed) else None

    def _on_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        self._preview_label.setText("Checking the backup…")
        run_off_thread(
            lambda: self._backups.preview_backup(selected.path),
            on_success=self._on_previewed,
            on_failure=self._on_failure,
        )

    def _on_previewed(self, preview: Any) -> None:
        if not isinstance(preview, RestorePreview):  # pragma: no cover - defensive
            return
        self._preview = preview
        self._preview_label.setText(preview.describe())
        may_manage = role_allows(self._role, Permission.MANAGE_DEVICE_SETTINGS)
        self._restore_button.setEnabled(preview.valid and may_manage)

    def _create(self) -> None:
        label = self._label.text().strip() or "manual"
        self._set_busy(True)
        self._status.setText("Creating backup…")
        run_off_thread(
            lambda: self._backups.create_backup(label=label, requester_role=self._role),
            on_success=self._on_created,
            on_failure=self._on_failure,
        )

    def _on_created(self, path: Any) -> None:
        self._set_busy(False)
        self._status.setText(f"Created backup {Path(str(path)).name}.")
        self._load_list()

    def _restore(self) -> None:
        preview = self._preview
        if preview is None or not preview.valid:
            return
        dialog = RestoreConfirmDialog(preview, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._status.setText("Restore cancelled: nothing was changed.")
            return
        self._set_busy(True)
        self._status.setText(f"Restoring {preview.path.name}…")
        run_off_thread(
            lambda: self._backups.restore_backup(
                preview.path, confirmed=True, requester_role=self._role
            ),
            on_success=self._on_restored,
            on_failure=self._on_failure,
        )

    def _on_restored(self, result: Any) -> None:
        self._set_busy(False)
        detail = getattr(result, "detail", "Restore finished.")
        self._status.setText(f"{detail} The backup list below is current.")
        self._load_list()
        self._load_offline()

    def _on_failure(self, message: str) -> None:
        self._set_busy(False)
        self._status.setText(message)

    def _set_busy(self, busy: bool) -> None:
        may_manage = role_allows(self._role, Permission.MANAGE_DEVICE_SETTINGS)
        self._create_button.setEnabled(not busy and may_manage)
        self._refresh_offline_button.setEnabled(not busy)
        self._restore_button.setEnabled(
            not busy and may_manage and self._preview is not None and self._preview.valid
        )

    # -- Qt -------------------------------------------------------------------

    def _table_widget(self) -> QTableWidget:
        return self._table
