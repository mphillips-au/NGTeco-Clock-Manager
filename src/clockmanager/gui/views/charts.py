"""A small painted line chart for the dashboard.

The mock-ups call for an attendance trend on the dashboard. Rather than take
on a charting dependency for one graph, this draws it: a handful of series
over a shared set of day labels, with a grid, an axis and a legend.

It is a display widget only — it derives nothing and reads nothing. The view
hands it points that were already computed from stored attendance, so it stays
usable from a future headless build's test suite and adds no dependency to the
packaging story (``PLAN.md``: keep external dependencies minimal).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from clockmanager.gui.theme import current_palette

__all__ = ["ChartSeries", "TrendChart"]


@dataclass(frozen=True, slots=True)
class ChartSeries:
    """One named line. ``colour`` is a palette token, never a literal."""

    name: str
    values: list[int]
    colour_token: str


class TrendChart(QWidget):
    """Plots one or more integer series against shared labels."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._labels: list[str] = []
        self._series: list[ChartSeries] = []
        self._empty_text = "No data yet."
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAccessibleName("Attendance trend chart")

    def set_data(
        self, labels: list[str], series: list[ChartSeries], *, empty_text: str = "No data yet."
    ) -> None:
        """Replace the chart contents and repaint.

        The accessible description carries the same figures as the drawing,
        because a painted chart is invisible to a screen reader.
        """
        self._labels = labels
        self._series = series
        self._empty_text = empty_text
        summary = "; ".join(
            f"{item.name}: {', '.join(str(value) for value in item.values)}" for item in series
        )
        self.setAccessibleDescription(f"{', '.join(labels)}. {summary}" if summary else empty_text)
        self.update()

    # -- painting -------------------------------------------------------------

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        palette = current_palette()

        if not self._series or not self._labels:
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            painter.end()
            return

        label_font = QFont(self.font())
        label_font.setPixelSize(11)
        painter.setFont(label_font)

        left, right, top, bottom = 38.0, 12.0, 10.0, 38.0
        plot = QRectF(
            left,
            top,
            max(1.0, self.width() - left - right),
            max(1.0, self.height() - top - bottom),
        )
        peak = max((max(item.values, default=0) for item in self._series), default=0)
        peak = max(peak, 1)
        # Round the top of the scale up so the axis reads in whole steps.
        step = max(1, -(-peak // 4))
        peak = step * 4

        self._draw_grid(painter, plot, palette, peak, step)
        self._draw_series(painter, plot, palette, peak)
        self._draw_legend(painter, plot, palette)
        painter.end()

    def _draw_grid(
        self, painter: QPainter, plot: QRectF, palette: Any, peak: int, step: int
    ) -> None:
        grid_pen = QPen(QColor(palette.border))
        grid_pen.setWidthF(1.0)
        for index in range(5):
            value = step * index
            y = plot.bottom() - (value / peak) * plot.height()
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(
                QRectF(0, y - 9, plot.left() - 6, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(value),
            )

        count = len(self._labels)
        painter.setPen(QColor(palette.text_muted))
        for index, label in enumerate(self._labels):
            x = self._x_for(plot, index, count)
            painter.drawText(
                QRectF(x - 30, plot.bottom() + 4, 60, 16),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

    def _draw_series(self, painter: QPainter, plot: QRectF, palette: Any, peak: int) -> None:
        count = len(self._labels)
        for item in self._series:
            colour = QColor(getattr(palette, item.colour_token))
            points = [
                QPointF(
                    self._x_for(plot, index, count),
                    plot.bottom() - (min(value, peak) / peak) * plot.height(),
                )
                for index, value in enumerate(item.values[:count])
            ]
            if not points:
                continue

            # A soft fill under the line gives the card the weight it has in
            # the design without hiding the gridlines behind it.
            fill = QColor(colour)
            fill.setAlpha(24)
            area = QPolygonF(
                [
                    QPointF(points[0].x(), plot.bottom()),
                    *points,
                    QPointF(points[-1].x(), plot.bottom()),
                ]
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPolygon(area)

            line = QPen(colour)
            line.setWidthF(2.0)
            line.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(line)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF(points))

            painter.setBrush(colour)
            painter.setPen(Qt.PenStyle.NoPen)
            for point in points:
                painter.drawEllipse(point, 2.6, 2.6)

    def _draw_legend(self, painter: QPainter, plot: QRectF, palette: Any) -> None:
        x = plot.left()
        y = plot.bottom() + 22
        for item in self._series:
            colour = QColor(getattr(palette, item.colour_token))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(QPointF(x + 4, y + 6), 4, 4)
            painter.setPen(QColor(palette.text_muted))
            width = painter.fontMetrics().horizontalAdvance(item.name)
            painter.drawText(
                QRectF(x + 13, y, width + 6, 14), Qt.AlignmentFlag.AlignLeft, item.name
            )
            x += width + 34

    @staticmethod
    def _x_for(plot: QRectF, index: int, count: int) -> float:
        if count <= 1:
            return plot.center().x()
        return plot.left() + (index / (count - 1)) * plot.width()
