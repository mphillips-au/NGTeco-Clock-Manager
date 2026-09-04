"""Icon and avatar drawing.

The application ships no image assets: every icon is drawn with the painter at
the size and colour it is needed, so there is nothing to package, nothing that
blurs on a high-DPI display and nothing that stays the wrong colour when the
theme changes.

Icons are deliberately plain line drawings rather than glyphs from a platform
icon font: a font that is missing on the machine renders as empty boxes, and
this application is meant to run on a Linux service host later as well.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)

__all__ = ["avatar_pixmap", "initials_for", "nav_icon"]

#: Icons are drawn on a 24x24 grid and scaled by the painter transform, so one
#: set of coordinates serves every size.
_GRID = 24.0


def _home(p: QPainter) -> None:
    p.drawPolyline(QPolygonF([QPointF(3, 11), QPointF(12, 3), QPointF(21, 11)]))
    p.drawPolyline(
        QPolygonF([QPointF(5.5, 10), QPointF(5.5, 20), QPointF(18.5, 20), QPointF(18.5, 10)])
    )


def _people(p: QPainter) -> None:
    p.drawEllipse(QPointF(9, 8.5), 3.5, 3.5)
    p.drawArc(QRectF(3, 13, 12, 12), 0, 180 * 16)
    p.drawArc(QRectF(13, 5.5, 7, 7), -80 * 16, 160 * 16)
    p.drawArc(QRectF(14, 13.5, 8, 10), 0, 120 * 16)


def _person(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 8), 4, 4)
    p.drawArc(QRectF(5, 13, 14, 14), 0, 180 * 16)


def _calendar(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.5, 5.5, 17, 15), 2, 2)
    p.drawLine(QPointF(3.5, 10), QPointF(20.5, 10))
    p.drawLine(QPointF(8, 3), QPointF(8, 7))
    p.drawLine(QPointF(16, 3), QPointF(16, 7))


def _play(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawPolyline(QPolygonF([QPointF(10, 8), QPointF(16, 12), QPointF(10, 16), QPointF(10, 8)]))


def _clock(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawPolyline(QPolygonF([QPointF(12, 7), QPointF(12, 12), QPointF(16, 14)]))


def _chart(p: QPainter) -> None:
    p.drawPolyline(QPolygonF([QPointF(3.5, 3.5), QPointF(3.5, 20.5), QPointF(20.5, 20.5)]))
    p.drawLine(QPointF(8, 17), QPointF(8, 11))
    p.drawLine(QPointF(12.5, 17), QPointF(12.5, 7))
    p.drawLine(QPointF(17, 17), QPointF(17, 13))


def _device(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(6.5, 2.5, 11, 19), 2, 2)
    p.drawLine(QPointF(6.5, 17), QPointF(17.5, 17))
    p.drawEllipse(QPointF(12, 19.2), 0.9, 0.9)
    p.drawEllipse(QPointF(12, 9), 3, 3)


def _document(p: QPainter) -> None:
    p.drawPolyline(
        QPolygonF(
            [
                QPointF(6, 3),
                QPointF(14, 3),
                QPointF(18, 7),
                QPointF(18, 21),
                QPointF(6, 21),
                QPointF(6, 3),
            ]
        )
    )
    p.drawPolyline(QPolygonF([QPointF(14, 3), QPointF(14, 7), QPointF(18, 7)]))
    p.drawLine(QPointF(9, 12), QPointF(15, 12))
    p.drawLine(QPointF(9, 16), QPointF(15, 16))


def _tools(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 3, 3)
    for x1, y1, x2, y2 in (
        (12, 3, 12, 7),
        (12, 17, 12, 21),
        (3, 12, 7, 12),
        (17, 12, 21, 12),
    ):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _archive(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.5, 4.5, 17, 5), 1.5, 1.5)
    p.drawPolyline(
        QPolygonF([QPointF(5.5, 9.5), QPointF(5.5, 20), QPointF(18.5, 20), QPointF(18.5, 9.5)])
    )
    p.drawLine(QPointF(10, 14), QPointF(14, 14))


def _shield(p: QPainter) -> None:
    p.drawPolyline(
        QPolygonF(
            [
                QPointF(12, 3),
                QPointF(19.5, 6.5),
                QPointF(19.5, 12),
                QPointF(12, 21),
                QPointF(4.5, 12),
                QPointF(4.5, 6.5),
                QPointF(12, 3),
            ]
        )
    )
    p.drawPolyline(QPolygonF([QPointF(9, 12), QPointF(11.3, 14.5), QPointF(15.5, 9.8)]))


#: Navigation label → drawing. A label missing here simply gets no icon.
_ICONS: dict[str, Callable[[QPainter], None]] = {
    "Dashboard": _home,
    "Users": _people,
    "Attendance": _calendar,
    "Live events": _play,
    "Employees": _person,
    "Timesheets": _clock,
    "Reports": _chart,
    "Device settings": _device,
    "Audit log": _document,
    "Diagnostics": _tools,
    "Backup": _archive,
    "User accounts": _shield,
}


def nav_icon(label: str, colour: str, *, size: int = 18) -> QIcon:
    """Return the navigation icon for ``label``, drawn in ``colour``.

    Icons are painted per colour rather than tinted afterwards, so the
    selected row's light glyph and the resting rows' muted glyph are both
    exactly the colour the theme asked for.
    """
    draw = _ICONS.get(label)
    if draw is None:
        return QIcon()

    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / _GRID, size / _GRID)
    pen = QPen(QColor(colour))
    pen.setWidthF(1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    draw(painter)
    painter.end()
    return QIcon(pixmap)


def initials_for(name: str) -> str:
    """Return up to two initials for a person's name.

    Falls back to the leading characters of whatever it is given, so a record
    holding only a user ID still produces a readable badge.
    """
    parts = [part for part in name.replace(".", " ").replace("-", " ").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def avatar_pixmap(name: str, background: str, foreground: str, *, size: int = 22) -> QPixmap:
    """A round initials badge, the way business software shows people.

    Drawing initials beats showing photographs the device does not hold: the
    badge identifies the row at a glance and carries no personal data beyond
    the name already displayed next to it.
    """
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(background))
    painter.drawEllipse(0, 0, size - 1, size - 1)

    font = QFont()
    font.setPixelSize(max(8, int(size * 0.42)))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(foreground))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, initials_for(name))
    painter.end()
    return pixmap
