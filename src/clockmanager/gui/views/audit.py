"""Audit log view.

Shows what this installation has done to devices: every user create, update and
delete attempt, including the ones that were refused or failed.

The log is append-only and this view is read-only — there is no clear or delete
action here, and none should be added. Entries contain no credential; a PIN
change is recorded as an action, never as a value (``SECURITY.md``).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.gui.views.common import build_table, fill_table, page_header, run_off_thread
from clockmanager.services.audit import AuditEntry, AuditOutcome, AuditService

__all__ = ["AuditView"]

_HEADERS = ("When", "Who", "Action", "Outcome", "Device", "Target", "Detail")
_ALL_OUTCOMES = "All outcomes"


class AuditView(QWidget):
    """Displays recent audit entries."""

    def __init__(self, audit: AuditService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._audit = audit
        self._entries: list[AuditEntry] = []

        self._table = build_table(_HEADERS, self)

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter by user, action or detail…")
        self._filter.textChanged.connect(self._apply_filter)

        self._outcome = QComboBox(self)
        self._outcome.addItem(_ALL_OUTCOMES, "")
        for outcome in AuditOutcome:
            self._outcome.addItem(outcome.value.capitalize(), outcome.value)
        self._outcome.currentIndexChanged.connect(self._apply_filter)

        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.clicked.connect(self.refresh)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self._refresh_button)
        controls.addWidget(self._filter, stretch=1)
        controls.addWidget(self._outcome)

        layout = QVBoxLayout()
        layout.addWidget(
            page_header(
                "Audit log",
                "Every device change, export and sign-in, in the order it happened. The log is append-only and is never edited from here.",
            )
        )
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(
            QLabel(
                "Every device write is recorded here, including attempts that were "
                "refused or that failed. Entries cannot be edited or removed.",
                self,
            )
        )
        self.setLayout(layout)
        fill_table(self._table, [], empty_message="Loading the audit log…")

    # No load here: like every other view, this one does its I/O when the
    # window navigates to it, never while it is being constructed.

    def refresh(self) -> None:
        """Reload recent entries off the UI thread."""
        self._refresh_button.setEnabled(False)
        run_off_thread(
            lambda: self._audit.recent(limit=500),
            on_success=self._on_loaded,
            on_failure=self._on_failure,
        )

    def _on_loaded(self, entries: Any) -> None:
        self._refresh_button.setEnabled(True)
        if not isinstance(entries, list):  # pragma: no cover - defensive
            return
        self._entries = entries
        self._status.setText(
            "No device changes have been recorded yet."
            if not entries
            else f"{len(entries)} recent entr{'y' if len(entries) == 1 else 'ies'}."
        )
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self._filter.text().strip().lower()
        outcome = str(self._outcome.currentData() or "")
        rows = [
            entry.as_row()
            for entry in self._entries
            if (not outcome or entry.outcome == outcome)
            and (not needle or needle in " ".join(entry.as_row()).lower())
        ]
        fill_table(
            self._table,
            rows,
            empty_message=(
                "No entries match these filters."
                if self._entries
                else "Nothing recorded yet. Actions appear here as they happen."
            ),
        )

    def _on_failure(self, message: str) -> None:
        self._refresh_button.setEnabled(True)
        self._status.setText(message)
