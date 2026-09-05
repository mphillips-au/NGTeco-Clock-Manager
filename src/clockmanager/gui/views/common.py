"""Shared view helpers.

Keeps page structure, table construction, empty/loading/error states, the
confirmation and notification patterns, and the "run this off the UI thread"
rule in one place, so every view behaves the same way. Views never hardcode a
colour: they set an ``objectName`` and :mod:`clockmanager.gui.theme` decides,
which is what keeps both themes legible.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from clockmanager.domain.auth import Permission, Role, can
from clockmanager.gui.theme import current_palette
from clockmanager.gui.workers import CallableWorker

__all__ = [
    "BusyGuard",
    "StatusKind",
    "build_table",
    "confirm",
    "fill_table",
    "muted_label",
    "notify",
    "page_header",
    "primary_button",
    "role_allows",
    "run_off_thread",
    "section_label",
    "set_column_sizing",
    "set_status",
    "tint_cell",
]

#: Status kinds for :func:`set_status`. Every view reports through these so
#: loading, empty, offline, success and error states look the same.
StatusKind = Literal["info", "success", "warning", "error", "loading"]


def set_status(label: QLabel, text: str, kind: StatusKind = "info") -> None:
    """Set a status label's text with a consistent kind.

    The ``objectName`` drives the theme stylesheet (``QLabel#StatusError``
    and friends), and the accessible description keeps screen readers
    announcing state changes as "Error: …" / "Loading: …".
    """
    label.setText(text)
    # ``objectName`` is a real Qt property, not an arbitrary dynamic
    # property.  QSS selectors such as ``QLabel#StatusError`` only match when
    # it is set with this method.
    label.setObjectName(f"Status{kind.capitalize()}")
    label.style().unpolish(label)
    label.style().polish(label)
    label.setAccessibleName(f"{kind.capitalize()}: {text}" if text else "")


def section_label(text: str, parent: QWidget | None = None) -> QLabel:
    """A bold section heading."""
    label = QLabel(text, parent)
    label.setObjectName("SectionLabel")
    return label


def muted_label(text: str, parent: QWidget | None = None) -> QLabel:
    """Secondary explanatory text, dimmed by the theme."""
    label = QLabel(text, parent)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


def page_header(title: str, subtitle: str = "", parent: QWidget | None = None) -> QWidget:
    """The title block every screen opens with.

    One heading and one line saying what the screen is for, then a hairline.
    Consistent page openings are most of what separates a business product
    from a stack of forms, and the heading gives assistive technology a
    landmark for the screen.
    """
    header = QWidget(parent)
    layout = QVBoxLayout(header)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(2)

    title_label = QLabel(title, header)
    title_label.setObjectName("PageTitle")
    title_label.setAccessibleName(f"{title} page")
    layout.addWidget(title_label)

    if subtitle:
        subtitle_label = QLabel(subtitle, header)
        subtitle_label.setObjectName("PageSubtitle")
        subtitle_label.setWordWrap(True)
        layout.addWidget(subtitle_label)

    divider = QFrame(header)
    divider.setObjectName("PageDivider")
    divider.setFrameShape(QFrame.Shape.HLine)
    divider.setFixedHeight(1)
    layout.addWidget(divider)
    return header


def primary_button(text: str, parent: QWidget | None = None) -> QPushButton:
    """The one obvious action on a screen, filled with the accent colour."""
    button = QPushButton(text, parent)
    button.setObjectName("Primary")
    return button


def build_table(
    headers: Sequence[str],
    parent: QWidget | None = None,
    *,
    sortable: bool = True,
    stretch_columns: Sequence[int] | None = None,
) -> QTableWidget:
    """A read-only, non-editable table with sensible defaults.

    Pass ``sortable=False`` for label/value tables, where the row order is
    meaningful and alphabetical sorting would scramble it. ``stretch_columns``
    names the columns that should absorb spare width; the rest size to their
    contents, which stops a timestamp column being padded to the same width
    as a one-character direction column.

    Tables select whole rows with single selection (keyboard arrows move,
    Enter/Space opens where a view offers it), never show the grid, and
    expose header names for assistive technology.
    """
    table = QTableWidget(0, len(headers), parent)
    table.setHorizontalHeaderLabels(list(headers))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setTabKeyNavigation(True)
    table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    table.setSortingEnabled(sortable)
    table.setAccessibleName(", ".join(headers))
    table.setCornerButtonEnabled(False)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    # Content-based column widths otherwise measure every row, which is
    # visibly slow once a table holds thousands of punches. Twenty rows is
    # plenty to pick a sensible width.
    header.setResizeContentsPrecision(20)
    # Headers left-align to sit over left-aligned cell text.
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    # Remembered so a placeholder row can switch to equal columns and the
    # configured sizing can be restored when real rows arrive.
    table.setProperty("stretchColumns", list(stretch_columns or ()))
    set_column_sizing(table, stretch_columns)
    return table


def set_column_sizing(table: QTableWidget, stretch_columns: Sequence[int] | None = None) -> None:
    """Size columns to content, letting ``stretch_columns`` take the slack.

    With no stretch columns every column shares the width equally, which is
    the right default for two-column label/value tables and wrong for a wide
    record table — hence the explicit argument.
    """
    header = table.horizontalHeader()
    if not stretch_columns:
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return
    wanted = set(stretch_columns)
    for column in range(table.columnCount()):
        mode = (
            QHeaderView.ResizeMode.Stretch
            if column in wanted
            else QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(column, mode)


def fill_table(
    table: QTableWidget,
    rows: Sequence[Sequence[str]],
    *,
    empty_message: str | None = None,
) -> None:
    """Replace a table's contents. Sorting is suspended during the fill.

    With no rows and an ``empty_message``, the table shows one centred,
    dimmed, unselectable line spanning every column, rather than a row of
    em-dashes pretending to be data.
    """
    was_sorting = table.isSortingEnabled()
    table.setSortingEnabled(False)
    table.clearContents()
    table.clearSpans()

    if not rows and empty_message is not None:
        # A spanned message is as wide as its text; content-sized columns
        # would inherit that width and push the last column off-screen.
        set_column_sizing(table, None)
        table.setRowCount(1)
        placeholder = QTableWidgetItem(empty_message)
        placeholder.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setFlags(Qt.ItemFlag.ItemIsEnabled)  # visible, never selectable
        placeholder.setForeground(QColor(current_palette().text_muted))
        table.setItem(0, 0, placeholder)
        if table.columnCount() > 1:
            table.setSpan(0, 0, 1, table.columnCount())
        table.setSortingEnabled(was_sorting)
        return

    stretch = table.property("stretchColumns")
    set_column_sizing(table, stretch if isinstance(stretch, list) else None)
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(value)
            table.setItem(row_index, column_index, item)

    table.setSortingEnabled(was_sorting)


def tint_cell(table: QTableWidget, row: int, column: int, token: str) -> None:
    """Colour one cell from a palette token (``"success"``, ``"badge_in"``…).

    Table items carry no stylesheet, so semantic colour has to be painted.
    Reading the token from the active palette keeps it legible in both themes.
    """
    item = table.item(row, column)
    if item is None:
        return
    item.setForeground(QColor(getattr(current_palette(), token)))


def confirm(
    parent: QWidget,
    title: str,
    question: str,
    *,
    detail: str = "",
    confirm_label: str = "Confirm",
    destructive: bool = False,
) -> bool:
    """Ask before doing something the operator cannot undo.

    The affirmative button is labelled with the action ("Delete user"), never
    a bare "Yes", and Cancel is the default so Enter or Escape is always the
    safe answer.
    """
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(question)
    if detail:
        box.setInformativeText(detail)
    box.setIcon(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question)
    accept = box.addButton(confirm_label, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is accept


def notify(parent: QWidget, message: str, *, kind: StatusKind = "success") -> None:
    """Show a brief, non-blocking confirmation in the corner of the window.

    Successful work should not need to be dismissed, so this never steals
    focus and never blocks: it fades in at the bottom-right of the window and
    removes itself. Failures stay in the view's status line as well, so the
    operator can still read them after the toast is gone.
    """
    window = parent.window()
    toast = QFrame(window)
    toast.setObjectName("ToastError" if kind == "error" else "Toast")
    toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    layout = QHBoxLayout(toast)
    layout.setContentsMargins(14, 10, 14, 10)
    label = QLabel(message, toast)
    label.setWordWrap(True)
    label.setMaximumWidth(360)
    layout.addWidget(label)
    toast.setAccessibleName(f"{kind.capitalize()}: {message}")
    toast.adjustSize()

    margin = 18
    toast.move(
        max(margin, window.width() - toast.width() - margin),
        max(margin, window.height() - toast.height() - margin * 2),
    )
    toast.show()
    toast.raise_()
    QTimer.singleShot(4000, toast.deleteLater)


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
