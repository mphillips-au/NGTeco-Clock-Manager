"""Derived reports and display-safe exports (PHASE 06).

Pure Python: no PySide6, no SQLAlchemy, no sockets.

Design notes:

* Reports are derived views over immutable stored attendance and the
  append-only audit/sync history. Building a report never mutates a punch,
  a sync row or an audit row -- the service layer only reads.
* A :class:`Report` is a titled table: column names plus string rows. All
  values are display-safe by construction: no communication password, no
  credential region, no biometric template and no internal event key ever
  appears in a column.
* Exporters (CSV/JSON/XLSX/PDF) are pure transformations of a
  :class:`Report` into bytes. XLSX is a minimal Office Open XML workbook
  written with the standard library (``zipfile``) so the product gains no
  new runtime dependency; PDF is a minimal single-font PDF written the same
  way. Both are deliberately simple -- one sheet / monospaced tables --
  and covered by tests that open the bytes back up.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from xml.sax.saxutils import escape as _xml_escape

__all__ = [
    "ExportFormat",
    "Report",
    "ReportFilter",
    "ReportType",
    "export_report",
    "report_to_csv_text",
    "report_to_json_text",
    "report_to_pdf_bytes",
    "report_to_xlsx_bytes",
]


class ReportType(StrEnum):
    """Every report this product can build."""

    DAILY_ATTENDANCE = "daily_attendance"
    EMPLOYEE_TIMESHEET = "employee_timesheet"
    WEEKLY_SUMMARY = "weekly_summary"
    PAY_PERIOD_SUMMARY = "pay_period_summary"
    EXCEPTIONS = "exceptions"
    DEVICE_ACTIVITY = "device_activity"
    SYNC_HISTORY = "sync_history"
    AUDIT = "audit"


class ExportFormat(StrEnum):
    """File formats a report can be exported to."""

    CSV = "csv"
    XLSX = "xlsx"
    PDF = "pdf"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class ReportFilter:
    """What subset of stored data a report covers.

    All fields are optional except the date window for the attendance-based
    reports. ``start``/``end`` are inclusive calendar days in device-local
    wall time (the same convention as timesheets). ``device_id`` selects one
    stored device; ``employee_id`` selects one business employee;
    ``user_id`` selects one canonical device user ID; ``department`` matches
    the employee department case-insensitively; ``punch``/``status`` match
    raw device values; ``exception_only`` keeps only rows that carry an
    exception flag.
    """

    start: date | None = None
    end: date | None = None
    employee_id: int | None = None
    user_id: str | None = None
    device_id: int | None = None
    department: str = ""
    punch: int | None = None
    status: int | None = None
    exception_only: bool = False
    include_inactive: bool = True

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("ReportFilter end must not precede start")
        if self.employee_id is not None and self.employee_id <= 0:
            raise ValueError("employee_id must be positive")
        if self.device_id is not None and self.device_id <= 0:
            raise ValueError("device_id must be positive")
        if self.user_id is not None and not self.user_id.strip():
            raise ValueError("user_id must not be blank")

    def describe(self) -> str:
        """Human-readable filter summary for report titles and filenames."""
        parts: list[str] = []
        if self.start is not None and self.end is not None:
            parts.append(f"{self.start.isoformat()} to {self.end.isoformat()}")
        elif self.start is not None:
            parts.append(f"from {self.start.isoformat()}")
        elif self.end is not None:
            parts.append(f"until {self.end.isoformat()}")
        if self.employee_id is not None:
            parts.append(f"employee #{self.employee_id}")
        if self.user_id:
            parts.append(f"user {self.user_id}")
        if self.device_id is not None:
            parts.append(f"device #{self.device_id}")
        if self.department.strip():
            parts.append(f"dept {self.department.strip()}")
        if self.punch is not None:
            parts.append(f"punch {self.punch}")
        if self.status is not None:
            parts.append(f"status {self.status}")
        if self.exception_only:
            parts.append("exceptions only")
        return "; ".join(parts) or "all stored data"


@dataclass(frozen=True, slots=True)
class Report:
    """A derived, display-safe table. Rows are strings, ready to render."""

    report_type: ReportType
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...] = ()
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    summary: str = ""

    def __post_init__(self) -> None:
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError(
                    f"Row has {len(row)} cells but the report has {len(self.columns)} columns"
                )

    def as_dicts(self) -> list[dict[str, str]]:
        """Rows as column-name dictionaries (JSON export and API future)."""
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


def report_to_csv_text(report: Report) -> str:
    """Render a report as CSV text (UTF-8, with header row)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(list(report.columns))
    for row in report.rows:
        writer.writerow(list(row))
    return buffer.getvalue()


def report_to_json_text(report: Report) -> str:
    """Render a report as indented JSON (metadata plus row dictionaries)."""
    payload = {
        "report_type": report.report_type.value,
        "title": report.title,
        "generated_at": report.generated_at.isoformat(),
        "summary": report.summary,
        "columns": list(report.columns),
        "rows": report.as_dicts(),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _xlsx_escape(text: str) -> str:
    return _xml_escape(text, {'"': "&quot;"})


def _xlsx_cell(ref: str, value: str) -> str:
    return (
        f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_xlsx_escape(value)}</t></is></c>'
    )


def report_to_xlsx_bytes(report: Report) -> bytes:
    """Render a report as a minimal single-sheet XLSX workbook.

    Uses inline strings (no shared-strings table) so the writer stays small
    and dependency-free. Verified by tests that unzip the bytes and read the
    sheet XML back.
    """
    header_cells = "".join(
        _xlsx_cell(f"{_column_letter(i)}1", name) for i, name in enumerate(report.columns)
    )
    sheet_rows = [f'<row r="1">{header_cells}</row>']
    for r, row in enumerate(report.rows, start=2):
        cells = "".join(_xlsx_cell(f"{_column_letter(i)}{r}", value) for i, value in enumerate(row))
        sheet_rows.append(f'<row r="{r}">{cells}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(sheet_rows) + "</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.worksheet+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()


def _column_letter(index: int) -> str:
    """Zero-based column index to Excel letters (0 -> A, 27 -> AB)."""
    letters = ""
    value = index
    while True:
        letters = chr(ord("A") + value % 26) + letters
        value = value // 26 - 1
        if value < 0:
            return letters


def report_to_pdf_bytes(report: Report) -> bytes:
    """Render a report as a minimal PDF (Helvetica, one or more pages).

    Deliberately plain: title, summary, then one monospaced line per row
    with columns separated by ``|``. Long lines are truncated to the page
    width rather than wrapped, so the byte layout stays predictable and the
    tests can assert the ``%PDF`` header plus the title text.
    """
    lines: list[str] = [report.title, report.summary or report.report_type.value, ""]
    if report.columns:
        lines.append(" | ".join(report.columns))
        lines.append("-" * min(180, max(20, len(lines[-1]))))
    for row in report.rows:
        lines.append(" | ".join(row))
    if len(lines) == 3:  # columns were empty: still a valid one-line report
        lines.append("(no rows)")
    if not report.rows:
        lines.append("(no rows)")
    return _lines_to_pdf_bytes(lines)


def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _lines_to_pdf_bytes(lines: list[str], *, per_page: int = 45) -> bytes:
    """Lay plain text lines out into PDF pages (Helvetica 10pt)."""
    pages: list[list[str]] = [lines[i : i + per_page] for i in range(0, len(lines), per_page)] or [
        ["(empty report)"]
    ]
    objects: list[bytes] = []
    # Object 1: catalog; 2: pages; then per page: page object + content stream.
    page_refs: list[str] = []
    obj_no = 3
    for _ in pages:
        page_refs.append(f"{obj_no} 0 R")
        obj_no += 2
    catalog = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    kids = " ".join(page_refs)
    pages_obj = f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>\nendobj\n".encode(
        "ascii"
    )
    objects.append(catalog)
    objects.append(pages_obj)
    obj_no = 3
    for page_lines in pages:
        content_lines = ["BT /F1 10 Tf 50 770 Td 13 TL"]
        for line in page_lines:
            safe = _pdf_escape(line[:180])
            content_lines.append(f"({safe}) Tj T*")
        content_lines.append("ET")
        stream = "\n".join(content_lines).encode("latin-1", errors="replace")
        page_obj = (
            f"{obj_no} 0 obj\n<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 612 792] /Resources << /Font << /F1 {obj_no + 2} 0 R >> >> "
            f"/Contents {obj_no + 1} 0 R >>\nendobj\n".encode("ascii")
        )
        stream_obj = (
            f"{obj_no + 1} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream\nendobj\n"
        )
        objects.append(page_obj)
        objects.append(stream_obj)
        obj_no += 2
    font_obj = (
        f"{obj_no} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n".encode(
            "ascii"
        )
    )
    # The font object number was referenced above as obj_no; that holds because
    # exactly 2 objects per page were appended after the first two.
    objects.append(font_obj)
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(output.tell())
        output.write(obj)
    xref_start = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF"
        ).encode("ascii")
    )
    return output.getvalue()


def export_report(report: Report, fmt: ExportFormat) -> tuple[bytes, str, str]:
    """Export a report, returning ``(data, suffix, mime_type)``."""
    match fmt:
        case ExportFormat.CSV:
            return report_to_csv_text(report).encode("utf-8"), "csv", "text/csv"
        case ExportFormat.JSON:
            return report_to_json_text(report).encode("utf-8"), "json", "application/json"
        case ExportFormat.XLSX:
            return (
                report_to_xlsx_bytes(report),
                "xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        case ExportFormat.PDF:
            return report_to_pdf_bytes(report), "pdf", "application/pdf"
