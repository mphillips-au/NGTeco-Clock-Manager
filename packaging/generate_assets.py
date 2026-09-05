"""Generate application icons for Windows packaging.

Draws a crisp clock and attendance badge icon at multiple standard Windows
resolutions (16, 24, 32, 48, 64, 128, 256) and saves both multi-resolution
.ico and .png formats.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
)


def render_icon_image(size: int) -> QImage:
    """Render a single icon image at the requested pixel dimension."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    scale = size / 256.0

    # 1. Background rounded rect with deep slate gradient
    bg_rect = QRectF(8 * scale, 8 * scale, 240 * scale, 240 * scale)
    gradient = QLinearGradient(0, 0, 0, 256 * scale)
    gradient.setColorAt(0.0, QColor(30, 41, 59))  # Slate 800
    gradient.setColorAt(1.0, QColor(15, 23, 42))  # Slate 900

    border_pen = QPen(QColor(56, 189, 248), 6 * scale)  # Sky 400 accent border
    painter.setPen(border_pen)
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(bg_rect, 48 * scale, 48 * scale)

    # 2. Clock face circle
    center = QPointF(120 * scale, 120 * scale)
    radius = 80 * scale

    clock_face_grad = QLinearGradient(0, 40 * scale, 0, 200 * scale)
    clock_face_grad.setColorAt(0.0, QColor(51, 65, 85))  # Slate 700
    clock_face_grad.setColorAt(1.0, QColor(30, 41, 59))  # Slate 800

    painter.setPen(QPen(QColor(148, 163, 184), 3 * scale))
    painter.setBrush(QBrush(clock_face_grad))
    painter.drawEllipse(center, radius, radius)

    # 3. Dial tick marks (12, 3, 6, 9)
    painter.setPen(QPen(QColor(241, 245, 249), 4 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(center.x(), center.y() - radius + 10 * scale), QPointF(center.x(), center.y() - radius + 22 * scale))
    painter.drawLine(QPointF(center.x() + radius - 22 * scale, center.y()), QPointF(center.x() + radius - 10 * scale, center.y()))
    painter.drawLine(QPointF(center.x(), center.y() + radius - 22 * scale), QPointF(center.x(), center.y() + radius - 10 * scale))
    painter.drawLine(QPointF(center.x() - radius + 10 * scale, center.y()), QPointF(center.x() - radius + 22 * scale, center.y()))

    # 4. Clock hands (pointing at 9:00 - start of day punch IN)
    # Hour hand (pointing to 9 o'clock)
    painter.setPen(QPen(QColor(248, 250, 252), 6 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(center, QPointF(center.x() - 44 * scale, center.y()))

    # Minute hand (pointing to 12 o'clock)
    painter.setPen(QPen(QColor(56, 189, 248), 4.5 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(center, QPointF(center.x(), center.y() - 60 * scale))

    # Center pin
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(248, 250, 252)))
    painter.drawEllipse(center, 7 * scale, 7 * scale)

    # 5. Accent checkmark badge in lower-right corner (attendance verified)
    badge_center = QPointF(190 * scale, 190 * scale)
    badge_radius = 42 * scale

    badge_grad = QLinearGradient(0, 150 * scale, 0, 230 * scale)
    badge_grad.setColorAt(0.0, QColor(16, 185, 129))  # Emerald 500
    badge_grad.setColorAt(1.0, QColor(5, 150, 105))  # Emerald 600

    painter.setPen(QPen(QColor(15, 23, 42), 4 * scale))
    painter.setBrush(QBrush(badge_grad))
    painter.drawEllipse(badge_center, badge_radius, badge_radius)

    # Checkmark inside badge
    check_pen = QPen(QColor(255, 255, 255), 6 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(check_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    points = [
        QPointF(badge_center.x() - 16 * scale, badge_center.y()),
        QPointF(badge_center.x() - 4 * scale, badge_center.y() + 12 * scale),
        QPointF(badge_center.x() + 16 * scale, badge_center.y() - 12 * scale),
    ]
    painter.drawPolyline(QPolygonF(points))

    painter.end()
    return image


def save_multisize_ico(images: list[QImage], target_path: Path) -> None:
    """Package multiple PNG-encoded images into a standard Windows .ico file."""
    # ICO header: 2 bytes reserved (0), 2 bytes type (1 = icon), 2 bytes count
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = []
    png_datas = []

    offset = 6 + len(images) * 16
    for img in images:
        from PySide6.QtCore import QBuffer, QIODevice
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buffer, "PNG")
        png_data = bytes(buffer.data())
        png_datas.append(png_data)

        w = img.width() if img.width() < 256 else 0
        h = img.height() if img.height() < 256 else 0
        entry = struct.pack(
            "<BBBBHHII",
            w,
            h,
            0,  # colors
            0,  # reserved
            1,  # planes
            32,  # bpp
            len(png_data),
            offset,
        )
        entries.append(entry)
        offset += len(png_data)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("wb") as f:
        f.write(header)
        for e in entries:
            f.write(e)
        for d in png_datas:
            f.write(d)


def generate_assets(output_dir: Path | None = None) -> tuple[Path, Path]:
    """Generate clockmanager.ico and clockmanager.png."""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent / "assets"
    output_dir.mkdir(parents=True, exist_ok=True)

    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [render_icon_image(s) for s in sizes]

    png_path = output_dir / "clockmanager.png"
    images[-1].save(str(png_path), "PNG")

    ico_path = output_dir / "clockmanager.ico"
    save_multisize_ico(images, ico_path)

    return ico_path, png_path


if __name__ == "__main__":
    ico, png = generate_assets()
    print(f"Generated {ico} ({ico.stat().st_size} bytes)")
    print(f"Generated {png} ({png.stat().st_size} bytes)")
