"""Admin-only protocol diagnostics application service (PHASE 10).

The GUI calls this; it never touches the protocol layer directly
(``ARCHITECTURE.md``). Everything here is synchronous and PySide6-free.

What it does:

* :meth:`DiagnosticsService.connection_report` — timed connect, device
  clock vs local clock, and a disconnect/reconnect cycle, per step.
* :meth:`DiagnosticsService.protocol_trace` — one connected session that
  captures transport TX/RX (real hardware only; the mock has no socket),
  raw + parsed user records with the credential region zeroed inside the
  protocol layer, raw + parsed attendance, a short live-capture window and
  the capability report, every step timed.
* :meth:`DiagnosticsService.export_trace` — the trace as sanitized JSON,
  audited as ``diagnostics.export``.

Device failures are returned as failed results and recorded in the trace,
never raised past the GUI. Role refusal (non-admin) is raised: a refusal
is a policy decision, not a device outcome. Nothing here writes, deletes,
clears or resets anything — diagnostics is read-only by construction.
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from clockmanager import __version__
from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, require
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.models import SCHEMA_VERSION, utc_now
from clockmanager.protocol.capabilities import Capability
from clockmanager.protocol.errors import DeviceError
from clockmanager.protocol.trace import (
    AttendanceSnapshot,
    TraceEvent,
    TraceRecorder,
    UserSnapshot,
    capture_attendance_snapshot,
    capture_user_snapshot,
)
from clockmanager.services.audit import AuditAction, AuditOutcome, AuditService
from clockmanager.services.devices import DeviceProfile, DeviceService

__all__ = [
    "CapabilityReport",
    "ConnectionReport",
    "ConnectionStep",
    "DiagnosticsService",
    "LiveWindow",
    "ProtocolTrace",
]

_logger = get_logger(__name__)

#: Live-capture window bounds for a trace: long enough to see traffic, short
#: enough that an operator never waits on it.
MAX_LIVE_SECONDS: float = 30.0
#: Collected live events are capped; beyond that the trace counts, not stores.
MAX_LIVE_EVENTS: int = 50


@dataclass(frozen=True, slots=True)
class ConnectionStep:
    """One timed step of a connection report."""

    name: str
    duration_ms: float
    detail: str = ""

    def as_row(self) -> list[str]:
        return [self.name, f"{self.duration_ms:.1f} ms", self.detail]


@dataclass(frozen=True, slots=True)
class ConnectionReport:
    """Timed connect/read/reconnect cycle. A device failure is a result."""

    ok: bool
    profile_name: str
    endpoint: str
    steps: tuple[ConnectionStep, ...] = ()
    error: str = ""
    error_type: str | None = None

    @property
    def summary(self) -> str:
        if not self.ok:
            return f"Connection diagnostics failed: {self.error}"
        total = sum(step.duration_ms for step in self.steps)
        return f"Connection diagnostics passed in {total:.0f} ms over {len(self.steps)} steps."

    def as_rows(self) -> list[tuple[str, str]]:
        rows = [("Result", "Passed" if self.ok else "Failed"), ("Detail", self.summary)]
        rows.extend(
            (step.name, f"{step.duration_ms:.1f} ms — {step.detail}") for step in self.steps
        )
        return rows


@dataclass(frozen=True, slots=True)
class LiveWindow:
    """What a short live-capture listen observed."""

    requested_seconds: float
    collected: int
    idle_timeouts: int
    events: tuple[dict[str, str], ...] = ()
    truncated: bool = False
    skipped: bool = False
    error: str = ""


@dataclass(frozen=True, slots=True)
class ProtocolTrace:
    """One connected diagnostic session, safe to display and to export."""

    ok: bool
    profile_name: str
    endpoint: str
    started_at: datetime
    total_ms: float
    transport_note: str
    events: tuple[TraceEvent, ...] = ()
    users: UserSnapshot | None = None
    attendance: AttendanceSnapshot | None = None
    live: LiveWindow | None = None
    capabilities: tuple[tuple[str, str, str], ...] = ()
    #: Name/value pairs for the device settings the trace could read. Every
    #: one is from the allow-list in :mod:`clockmanager.protocol.options`, so
    #: none of them can carry a credential.
    options: tuple[tuple[str, str], ...] = ()
    #: Capacity and usage lines from ``CMD_GET_FREE_SIZES``.
    storage: tuple[tuple[str, str], ...] = ()
    #: The device's own operation-log entries, as display rows.
    operation_log: tuple[tuple[str, str, str], ...] = ()
    error: str = ""
    error_type: str | None = None

    @property
    def summary(self) -> str:
        if not self.ok:
            return f"Protocol trace failed: {self.error}"
        users = f"{self.users.record_count} user(s)" if self.users is not None else "users unread"
        attendance = (
            f"{self.attendance.parsed_count} event(s)"
            if self.attendance is not None
            else "none read"
        )
        return (
            f"Trace of {self.endpoint} took {self.total_ms:.0f} ms: "
            f"{users}, attendance {attendance}."
        )


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    """Capability/support/reason triples with the profile they describe."""

    profile_name: str
    endpoint: str
    rows: tuple[tuple[str, str, str], ...] = ()

    def as_rows(self) -> list[tuple[str, str]]:
        return [(name, f"{support.upper()} — {reason}") for name, support, reason in self.rows]


class DiagnosticsService:
    """Runs read-only diagnostic sessions against one stored profile."""

    def __init__(self, devices: DeviceService, audit: AuditService) -> None:
        self._devices = devices
        self._audit = audit

    # -- connection diagnostics -----------------------------------------------

    def connection_report(
        self, profile: DeviceProfile, *, requester_role: Role | str | None = None
    ) -> ConnectionReport:
        """Timed connect, clock read and reconnect cycle.

        Stores the reported identity and last-seen stamp on success, like a
        connection test does. Never raises for a device problem.
        """
        if requester_role is not None:
            require(requester_role, Permission.VIEW_DIAGNOSTICS)
        steps: list[ConnectionStep] = []
        try:
            device = self._devices.build(profile)
        except (DeviceError, ClockManagerError) as exc:
            return ConnectionReport(
                ok=False,
                profile_name=profile.name,
                endpoint=profile.endpoint,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        try:
            started = time.perf_counter()
            try:
                info = device.connect()
            except (DeviceError, ClockManagerError) as exc:
                return self._failed(profile, steps, exc)
            connect_ms = (time.perf_counter() - started) * 1000.0
            steps.append(
                ConnectionStep(
                    "Connect + snapshot",
                    connect_ms,
                    f"{info.identity.model or 'unknown model'} / "
                    f"{info.identity.firmware_version or 'unknown firmware'}",
                )
            )
            self._devices.record_identity(profile, info)
            self._devices.mark_seen(profile)

            started = time.perf_counter()
            try:
                device_time = device.get_device_time()
            except (DeviceError, ClockManagerError) as exc:
                return self._failed(profile, steps, exc)
            clock_ms = (time.perf_counter() - started) * 1000.0
            drift = abs((datetime.now() - device_time).total_seconds())  # noqa: DTZ005
            steps.append(
                ConnectionStep(
                    "Device clock",
                    clock_ms,
                    f"{device_time.isoformat(sep=' ')} (differs from this "
                    f"computer by {drift:.0f} s; device time is local, not UTC)",
                )
            )

            started = time.perf_counter()
            try:
                device.disconnect()
                device.connect()
            except (DeviceError, ClockManagerError) as exc:
                return self._failed(profile, steps, exc)
            reconnect_ms = (time.perf_counter() - started) * 1000.0
            steps.append(
                ConnectionStep(
                    "Reconnect",
                    reconnect_ms,
                    "Dropped and re-established the connection; "
                    "automatic reconnect is "
                    f"{'on' if profile.auto_reconnect else 'off'} "
                    f"(timeout {profile.timeout_seconds:.1f} s).",
                )
            )
        finally:
            try:
                device.disconnect()
            except Exception:
                _logger.exception(
                    "Disconnect after diagnostics failed",
                    extra={"device": profile.name},
                )
        _logger.info(
            "Connection diagnostics finished",
            extra={"device": profile.name, "steps": len(steps)},
        )
        return ConnectionReport(
            ok=True, profile_name=profile.name, endpoint=profile.endpoint, steps=tuple(steps)
        )

    @staticmethod
    def _failed(
        profile: DeviceProfile, steps: list[ConnectionStep], exc: Exception
    ) -> ConnectionReport:
        _logger.warning(
            "Connection diagnostics failed",
            extra={"device": profile.name, "error": str(exc)},
        )
        return ConnectionReport(
            ok=False,
            profile_name=profile.name,
            endpoint=profile.endpoint,
            steps=tuple(steps),
            error=str(exc),
            error_type=type(exc).__name__,
        )

    # -- protocol trace ---------------------------------------------------------

    def protocol_trace(
        self,
        profile: DeviceProfile,
        *,
        live_seconds: float = 0.0,
        max_records: int = 25,
        requester_role: Role | str | None = None,
    ) -> ProtocolTrace:
        """Capture one connected session: TX/RX, raw + parsed records, timing.

        ``live_seconds`` (0-30) appends a short live-capture listen; 0 skips
        it. ``max_records`` caps stored per-record detail (counts are always
        complete). Never raises for a device problem.
        """
        if requester_role is not None:
            require(requester_role, Permission.VIEW_DIAGNOSTICS)
        if not 0.0 <= live_seconds <= MAX_LIVE_SECONDS:
            raise ClockManagerError(
                f"Live listen must be between 0 and {MAX_LIVE_SECONDS:.0f} seconds."
            )
        if max_records < 1:
            raise ClockManagerError("At least one record must be shown.")

        started_at = utc_now()
        total_started = time.perf_counter()
        recorder = TraceRecorder()
        try:
            device, transport_note = self._devices.build_traced(profile, recorder)
        except (DeviceError, ClockManagerError) as exc:
            return self._trace_failed(profile, started_at, total_started, recorder, "", exc)

        users: UserSnapshot | None = None
        attendance: AttendanceSnapshot | None = None
        live: LiveWindow | None = None
        try:
            with recorder.timed("session", "LOCAL", "Connect"):
                info = device.connect()
            self._devices.record_identity(profile, info)
            self._devices.mark_seen(profile)

            with recorder.timed("session", "LOCAL", "Device snapshot"):
                identity_rows = info.as_rows()
            recorder.record(
                "session",
                "LOCAL",
                "Identity",
                detail="; ".join(f"{name}={value}" for name, value in identity_rows),
            )
            with recorder.timed("session", "LOCAL", "Device clock"):
                device_time = device.get_device_time()
            recorder.record(
                "session",
                "LOCAL",
                "Clock comparison",
                detail=f"device {device_time.isoformat(sep=' ')} vs "
                f"local {datetime.now().isoformat(sep=' ', timespec='seconds')} "  # noqa: DTZ005
                "(device time is local, not UTC)",
            )

            with recorder.timed("session", "RX", "User records (raw + parsed)"):
                try:
                    users = capture_user_snapshot(device, max_records=max_records)
                except (DeviceError, ValueError) as exc:
                    recorder.record("session", "LOCAL", "User records failed", detail=str(exc))

            with recorder.timed("session", "RX", "Attendance (raw + parsed)"):
                try:
                    attendance = capture_attendance_snapshot(device, max_events=max_records)
                except (DeviceError, ValueError) as exc:
                    recorder.record("session", "LOCAL", "Attendance failed", detail=str(exc))

            with recorder.timed("session", "RX", "Fingerprint slots"):
                recorder.record(
                    "session",
                    "LOCAL",
                    "Fingerprint slots",
                    detail=_describe_fingerprint_slots(device),
                )

            with recorder.timed("session", "RX", "Storage"):
                storage_rows = _read_storage_rows(device)
            recorder.record(
                "session",
                "LOCAL",
                "Storage",
                detail="; ".join(f"{label}={value}" for label, value in storage_rows)
                or "Not available on this device.",
            )

            with recorder.timed("session", "RX", "Device settings"):
                option_rows = _read_option_rows(device)
            recorder.record(
                "session",
                "LOCAL",
                "Device settings",
                detail=f"{sum(1 for _, value in option_rows if value)} of "
                f"{len(option_rows)} allow-listed options answered"
                if option_rows
                else "Not available on this device.",
            )

            with recorder.timed("session", "RX", "Device operation log"):
                operation_rows = _read_operation_log_rows(device, limit=max_records)
            recorder.record(
                "session",
                "LOCAL",
                "Device operation log",
                detail=f"{len(operation_rows)} entry/entries shown. The device's own "
                "record of keypad activity; operation codes are reported as numbers "
                "because their meanings are not verified on this model."
                if operation_rows
                else "Not available on this device.",
            )

            if live_seconds > 0:
                live = self._listen(device, recorder, live_seconds)
            else:
                live = LiveWindow(requested_seconds=0.0, collected=0, idle_timeouts=0, skipped=True)

            with recorder.timed("session", "LOCAL", "Capabilities"):
                capabilities = tuple(device.capabilities.as_rows())

            with recorder.timed("session", "LOCAL", "Reconnect"):
                device.disconnect()
                device.connect()
        except (DeviceError, ClockManagerError) as exc:
            try:
                device.disconnect()
            except Exception:
                _logger.exception("Disconnect after failed trace", extra={"device": profile.name})
            return self._trace_failed(
                profile, started_at, total_started, recorder, transport_note, exc
            )
        try:
            device.disconnect()
        except Exception:
            _logger.exception("Disconnect after trace failed", extra={"device": profile.name})

        total_ms = (time.perf_counter() - total_started) * 1000.0
        _logger.info(
            "Protocol trace finished",
            extra={"device": profile.name, "events": len(recorder.events)},
        )
        return ProtocolTrace(
            ok=True,
            profile_name=profile.name,
            endpoint=profile.endpoint,
            started_at=started_at,
            total_ms=total_ms,
            transport_note=transport_note,
            events=tuple(recorder.events),
            users=users,
            attendance=attendance,
            live=live,
            capabilities=capabilities,
            options=option_rows,
            storage=storage_rows,
            operation_log=operation_rows,
        )

    def _listen(self, device: Any, recorder: TraceRecorder, live_seconds: float) -> LiveWindow:
        """Listen for live events until the deadline, then stop cleanly."""
        from clockmanager.domain.models import AttendanceEvent as _Event

        collected: list[_Event] = []
        idle = 0
        deadline = time.monotonic() + live_seconds
        error = ""
        with recorder.timed("live", "RX", f"Live listen ({live_seconds:.0f} s)"):
            try:
                stream = device.live_capture(timeout_seconds=min(2.0, live_seconds))
                try:
                    for event in stream:
                        if event is None:
                            idle += 1
                        else:
                            collected.append(event)
                        if len(collected) >= MAX_LIVE_EVENTS or time.monotonic() >= deadline:
                            break
                finally:
                    with suppress(DeviceError, ClockManagerError):
                        device.stop_live_capture()
                    stream.close()
            except (DeviceError, ClockManagerError) as exc:
                error = str(exc)
        shown = collected[:MAX_LIVE_EVENTS]
        return LiveWindow(
            requested_seconds=live_seconds,
            collected=len(collected),
            idle_timeouts=idle,
            events=tuple(
                {
                    "user_id": event.user_id,
                    "occurred_at": event.occurred_at.isoformat(sep=" "),
                    "punch": event.direction_label,
                    "status": str(event.status),
                }
                for event in shown
            ),
            truncated=len(collected) > len(shown),
            error=error,
        )

    def _trace_failed(
        self,
        profile: DeviceProfile,
        started_at: datetime,
        total_started: float,
        recorder: TraceRecorder,
        transport_note: str,
        exc: Exception,
    ) -> ProtocolTrace:
        _logger.warning("Protocol trace failed", extra={"device": profile.name, "error": str(exc)})
        return ProtocolTrace(
            ok=False,
            profile_name=profile.name,
            endpoint=profile.endpoint,
            started_at=started_at,
            total_ms=(time.perf_counter() - total_started) * 1000.0,
            transport_note=transport_note,
            events=tuple(recorder.events),
            error=str(exc),
            error_type=type(exc).__name__,
        )

    # -- capabilities -----------------------------------------------------------

    def capability_report(
        self, profile: DeviceProfile, *, requester_role: Role | str | None = None
    ) -> CapabilityReport:
        """Capability/support/reason triples for one profile. Touches no device."""
        if requester_role is not None:
            require(requester_role, Permission.VIEW_DIAGNOSTICS)
        return CapabilityReport(
            profile_name=profile.name,
            endpoint=profile.endpoint,
            rows=tuple(self._devices.capabilities(profile).as_rows()),
        )

    # -- export -----------------------------------------------------------------

    def export_trace(
        self, trace: ProtocolTrace, *, requester_role: Role | str | None = None
    ) -> tuple[bytes, str, str]:
        """Export a trace as sanitized JSON and audit the export.

        Sanitized by construction: user records carry redacted hex only, and
        no communication password, PIN, card or biometric value exists
        anywhere in the payload.
        """
        if requester_role is not None:
            require(requester_role, Permission.VIEW_DIAGNOSTICS)
        document = {
            "manifest": {
                "application": "NGTecoClockManager",
                "app_version": __version__,
                "schema_version": SCHEMA_VERSION,
                "created_at": utc_now().isoformat(),
                "profile": trace.profile_name,
                "endpoint": trace.endpoint,
                "ok": trace.ok,
            },
            "transport_note": trace.transport_note,
            "events": [event.as_row() for event in trace.events],
            "users": None
            if trace.users is None
            else {
                "record_count": trace.users.record_count,
                "total_bytes": trace.users.total_bytes,
                "truncated": trace.users.truncated,
                "records": [
                    {
                        "device_uid": record.device_uid,
                        "user_id": record.user_id,
                        "first_name": record.first_name,
                        "last_name": record.last_name,
                        "privilege": record.privilege,
                        "privilege_label": record.privilege_label,
                        "has_credential_data": record.has_credential_data,
                        "redacted_hex": record.redacted_hex,
                    }
                    for record in trace.users.records
                ],
            },
            "attendance": None
            if trace.attendance is None
            else {
                "payload_bytes": trace.attendance.payload_bytes,
                "declared_size": trace.attendance.declared_size,
                "record_size": trace.attendance.record_size,
                "parsed_count": trace.attendance.parsed_count,
                "truncated": trace.attendance.truncated,
                "note": trace.attendance.note,
                "error": trace.attendance.error,
                "events": list(trace.attendance.events),
            },
            "live": None
            if trace.live is None
            else {
                "requested_seconds": trace.live.requested_seconds,
                "collected": trace.live.collected,
                "idle_timeouts": trace.live.idle_timeouts,
                "truncated": trace.live.truncated,
                "skipped": trace.live.skipped,
                "error": trace.live.error,
                "events": list(trace.live.events),
            },
            "capabilities": [
                {"capability": name, "support": support, "reason": reason}
                for name, support, reason in trace.capabilities
            ],
            "storage": [{"counter": label, "value": value} for label, value in trace.storage],
            "options": [{"setting": label, "value": value} for label, value in trace.options],
            "operation_log": [
                {"when": when, "operation": operation, "detail": detail}
                for when, operation, detail in trace.operation_log
            ],
            "error": trace.error,
        }
        payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
        stamp = utc_now().strftime("%Y%m%d-%H%M%S")
        safe_profile = (
            "".join(
                char if char.isalnum() or char in ("-", "_") else "-" for char in trace.profile_name
            ).strip("-")[:40]
            or "device"
        )
        filename = f"diagnostics-{safe_profile}-{stamp}.json"
        self._audit.record(
            AuditAction.DIAGNOSTICS_EXPORT,
            AuditOutcome.SUCCEEDED,
            device_name=trace.profile_name,
            target=filename,
            detail=f"Exported diagnostics for {trace.profile_name} "
            f"({len(trace.events)} events, {len(payload)} bytes)",
        )
        _logger.info("Exported diagnostics", extra={"device": trace.profile_name})
        return payload, filename, "application/json"


def _read_storage_rows(device: Any) -> tuple[tuple[str, str], ...]:
    """Capacity and usage lines, or nothing if the device will not report them."""
    storage = _optional_read(device, Capability.READ_STORAGE, "read_storage")
    if storage is None:
        return ()
    return tuple(storage.as_rows())


def _read_option_rows(device: Any) -> tuple[tuple[str, str], ...]:
    """Allow-listed device settings as label/value pairs.

    Safe to export: the names are a fixed catalogue, and one that could carry a
    credential is refused inside the protocol layer before a request is built.
    An option the firmware does not have is reported as such rather than
    omitted, because "this model has no work codes" is a diagnostic answer.
    """
    options = _optional_read(device, Capability.READ_DEVICE_OPTIONS, "read_device_options")
    if options is None:
        return ()
    return tuple((option.label, option.display_value) for option in options)


def _read_operation_log_rows(device: Any, *, limit: int) -> tuple[tuple[str, str, str], ...]:
    """The most recent operation-log entries as display rows.

    Operation codes are shown as numbers. Only the timestamp inside a record is
    verified on this device (PHASE 15); naming the codes would be inventing
    meaning the investigation did not establish.
    """
    entries = _optional_read(device, Capability.READ_OPERATION_LOG, "read_operation_log")
    if not entries:
        return ()
    return tuple(
        (
            entry.occurred_label,
            entry.operation_label,
            f"operator UID {entry.operator_uid}, parameters {entry.parameters}",
        )
        for entry in entries[-limit:]
    )


def _optional_read(device: Any, capability: Capability, method: str) -> Any:
    """Call one read-only device method, or return ``None`` with a log line.

    A trace step must never take the whole trace down, and the three questions
    -- the implementation does not offer it, the capability withholds it, the
    device errored -- are all answered the same way here: this section is
    simply absent from the trace.
    """
    read = getattr(device, method, None)
    if read is None or not device.capabilities.supports(capability):
        return None
    try:
        return read()
    except (DeviceError, ValueError) as exc:
        _logger.warning(
            "Diagnostics section unavailable", extra={"section": method, "error": str(exc)}
        )
        return None


def _describe_fingerprint_slots(device: Any) -> str:
    """Summarise the fingerprint store for a trace, disclosing no template.

    Enumeration is proven on the real NG-MB1 (``PROTOCOL.md``, PHASE 15) and is
    a read like any other in this trace. Only the per-slot metadata reaches the
    detail string -- which user, which finger, how many bytes long -- because
    the template contents are biometric data and ``SECURITY.md`` forbids
    logging, exporting or persisting them. A device that does not answer is
    reported, not raised: a trace step must never take the whole trace down.
    """
    read_slots = getattr(device, "read_fingerprint_slots", None)
    if read_slots is None:
        return "Not available on this device implementation."
    if not device.capabilities.supports(Capability.READ_FINGERPRINT):
        return "Fingerprint enumeration is not available on this device."
    try:
        slots = read_slots()
    except (DeviceError, ValueError) as exc:
        return f"Could not enumerate: {exc}"
    if not slots:
        return "No fingerprints enrolled."
    return f"{len(slots)} enrolled: " + "; ".join(
        f"UID {slot.device_uid} finger {slot.finger_index} "
        f"({'valid' if slot.is_valid else 'invalid'}, {slot.template_bytes} template bytes)"
        for slot in slots
    )
