"""Shared view helpers.

Keeps table construction and the "run this off the UI thread" pattern in one
place so every view uses them consistently.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from clockmanager.domain.auth import Permission, Role, can
from clockmanager.gui.workers import CallableWorker

__all__ = [
    "BusyGuard",
    "build_table",
    "fill_table",
    "role_allows",
    "run_off_thread",
    "section_label",
]


def section_label(text: str, parent: QWidget | None = None) -> QLabel:
    """A bold section heading."""
    label = QLabel(text, parent)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


def build_table(
    headers: Sequence[str], parent: QWidget | None = None, *, sortable: bool = True
) -> QTableWidget:
    """A read-only, non-editable table with sensible defaults.

    Pass ``sortable=False`` for label/value tables, where the row order is
    meaningful and alphabetical sorting would scramble it.
    """
    table = QTableWidget(0, len(headers), parent)
    table.setHorizontalHeaderLabels(list(headers))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.setSortingEnabled(sortable)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    return table


def fill_table(table: QTableWidget, rows: Sequence[Sequence[str]]) -> None:
    """Replace a table's contents. Sorting is suspended during the fill."""
    was_sorting = table.isSortingEnabled()
    table.setSortingEnabled(False)
    table.clearContents()
    table.setRowCount(len(rows))

    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, column_index, item)

    table.resizeColumnsToContents()
    table.setSortingEnabled(was_sorting)


def run_off_thread(
    work: Callable[[], Any],
    *,
    on_success: Callable[[Any], None],
    on_failure: Callable[[str], None],
) -> None:
    """Run ``work`` on the global thread pool.

    Every device and database call in the GUI goes through here so nothing
    blocks the UI thread.
    """
    worker = CallableWorker(work)
    worker.signals.finished.connect(on_success)
    worker.signals.failed.connect(on_failure)
    QThreadPool.globalInstance().start(worker)


class BusyGuard:
    """Disables widgets while a background operation runs."""

    def __init__(self, widgets: Sequence[QWidget]) -> None:
        self._widgets = list(widgets)

    def begin(self) -> None:
        for widget in self._widgets:
            widget.setEnabled(False)

    def end(self) -> None:
        for widget in self._widgets:
            widget.setEnabled(True)


def role_allows(role: Role | str | None, permission: Permission) -> bool:
    """Return whether a view should enable a control for ``role``.

    ``None`` is the pre-login/test path without an interactive identity and
    keeps the legacy enabled state, matching the service layer's
    ``requester_role=None`` bypass. The GUI always passes a real role once
    someone is logged in.
    """
    if role is None:
        return True
    return can(role, permission)
