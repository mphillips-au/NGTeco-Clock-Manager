"""Attendance synchronisation application service (PHASE 04).

The GUI calls this; it never touches the protocol layer or SQLAlchemy
directly (``ARCHITECTURE.md``). Everything here is synchronous and
PySide6-free, so the future headless service syncs through exactly the same
code.

How it works:

* Every sync reads the device's whole stored log (the MB1 exposes no
  incremental API) and inserts only what is not already stored. That is the
  "incremental reconciliation": a full re-read that behaves incrementally
  because of duplicate detection. A repeated sync therefore inserts nothing.
* Duplicate detection is by deterministic event key first, then by the
  natural key, so rows stored before event keys existed still count.
* A punch is stored even when its user ID matches no known device user.
  The employee link is resolved in PHASE 05; dropping the punch would destroy
  evidence. The sync-time display name is snapshotted when the user is known.
* Direction comes from ``punch`` alone and is derived on display, never
  stored as a separate fact. ``status`` is preserved verbatim.
* When the device cannot be reached, the failure is recorded in the sync
  history and returned as a failed result rather than raised, so the GUI can
  report it. The next successful run re-reads the whole log, which is the
  offline recovery: anything missed while offline (or missed by live capture)
  arrives then.
* Connection retries inside one read belong to the protocol layer
  (``RetryPolicy`` with a reconnect hook). This service always disconnects,
  including when the device fails mid-read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, require
from clockmanager.domain.models import AttendanceEvent, describe_punch
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.database import Database
from clockmanager.persistence.models import DeviceRecord, SyncHistoryRecord, utc_now
from clockmanager.persistence.repositories import (
    AttendanceRepository,
    SyncHistoryRepository,
)
from clockmanager.protocol.errors import DeviceError
from clockmanager.services.devices import DeviceProfile, DeviceService
from clockmanager.sync.engine import plan_inserts
from clockmanager.sync.sources import SyncSource

__all__ = [
    "StoredAttendance",
    "SyncResult",
    "SyncService",
    "SyncSummary",
    "as_aware_utc",
]

_logger = get_logger(__name__)


def as_aware_utc(moment: datetime) -> datetime:
    """Normalise a stored timestamp to an aware UTC datetime.

    SQLite has no timezone type and returns naive datetimes on read
    (``STATUS.md``). Timestamps written as UTC come back naive; re-attaching
    UTC makes the round trip stable. Naive values are therefore assumed to be
    UTC. Device-local ``occurred_at`` values are *not* passed through here:
    they are naive by construction and must not be mislabelled as UTC.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class StoredAttendance:
    """One locally stored punch, ready to display. Contains no credential."""

    record_id: int
    device_id: int
    device_uid: int | None
    user_id: str
    employee_name: str | None
    occurred_at: datetime
    punch: int
    status: int
    received_at: datetime
    source: str
    event_key: str

    @property
    def direction_label(self) -> str:
        """IN/OUT from ``punch`` only; ``status`` is never interpreted."""
        return describe_punch(self.punch)

    @property
    def display_name(self) -> str:
        return self.employee_name or self.user_id


@dataclass(frozen=True, slots=True)
class SyncResult:
    """The outcome of one sync run. A device failure is a result, not a raise."""

    ok: bool
    device_id: int
    device_name: str
    source: str
    seen: int = 0
    new: int = 0
    duplicate: int = 0
    is_recovery: bool = False
    error: str = ""
    error_type: str | None = None

    @property
    def summary(self) -> str:
        if not self.ok:
            return f"Sync failed: {self.error}"
        if self.seen == 0:
            return "The device reported no attendance records."
        recovered = " (recovered after offline period)" if self.is_recovery else ""
        return (
            f"Read {self.seen} record(s){recovered}: "
            f"{self.new} new, {self.duplicate} already stored."
        )


@dataclass(frozen=True, slots=True)
class SyncSummary:
    """Local sync state for one device, for display and scheduling."""

    device_id: int
    stored_events: int
    last_success_at: datetime | None
    last_outcome: str | None
    last_error: str | None
    last_device_time: datetime | None

    def describe(self) -> str:
        if self.stored_events == 0 and self.last_success_at is None:
            return "Never synced. Press “Sync now” to store the device history locally."
        when = (
            self.last_success_at.isoformat(sep=" ", timespec="seconds")
            if self.last_success_at is not None
            else "never"
        )
        base = f"{self.stored_events} stored punch(es). Last successful sync: {when}."
        if self.last_outcome == "failed" and self.last_error:
            base += f" Last attempt failed: {self.last_error}"
        return base


class SyncService:
    """Stores device attendance locally with duplicate-safe reconciliation."""

    def __init__(self, database: Database, devices: DeviceService) -> None:
        self._database = database
        self._devices = devices

    # -- stored reads ---------------------------------------------------------

    def list_stored(self, profile: DeviceProfile, *, limit: int = 1000) -> list[StoredAttendance]:
        """Stored punches for one device, newest first. Never touches hardware."""
        device_id = self._require_device_id(profile)
        with self._database.session() as session:
            rows = AttendanceRepository(session).list_for_device(device_id, limit=limit)
            return [self._to_stored(row) for row in rows]

    def list_recent(self, *, limit: int = 1000) -> list[StoredAttendance]:
        """Stored punches across all devices, newest first."""
        with self._database.session() as session:
            rows = AttendanceRepository(session).recent(limit=limit)
            return [self._to_stored(row) for row in rows]

    def count_stored(self, profile: DeviceProfile) -> int:
        device_id = self._require_device_id(profile)
        with self._database.session() as session:
            return AttendanceRepository(session).count_for_device(device_id)

    def history(self, profile: DeviceProfile, *, limit: int = 50) -> list[SyncHistoryRecord]:
        """Sync runs for one device, newest first. Never touches hardware."""
        device_id = self._require_device_id(profile)
        with self._database.session() as session:
            return SyncHistoryRepository(session).recent_for_device(device_id, limit=limit)

    def recent_history(self, *, limit: int = 50) -> list[SyncHistoryRecord]:
        with self._database.session() as session:
            return SyncHistoryRepository(session).recent(limit=limit)

    def summary(self, profile: DeviceProfile) -> SyncSummary:
        """Local sync state for display and for background scheduling."""
        device_id = self._require_device_id(profile)
        with self._database.session() as session:
            attendance = AttendanceRepository(session)
            sync_history = SyncHistoryRepository(session)
            stored = attendance.count_for_device(device_id)
            newest = attendance.max_occurred_at(device_id)
            latest = sync_history.latest_for_device(device_id)
            latest_success = sync_history.latest_success_for_device(device_id)
        success_at: datetime | None = None
        if latest_success is not None and latest_success.finished_at is not None:
            success_at = as_aware_utc(latest_success.finished_at)
        return SyncSummary(
            device_id=device_id,
            stored_events=stored,
            last_success_at=success_at,
            last_outcome=None if latest is None else latest.outcome,
            last_error=None if latest is None else (latest.error or None),
            last_device_time=newest,
        )

    def is_due(self, profile: DeviceProfile, *, now: datetime | None = None) -> bool:
        """Whether a background sync should run now for ``profile``."""
        moment = now if now is not None else utc_now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        device_id = self._require_device_id(profile)
        with self._database.session() as session:
            latest = SyncHistoryRepository(session).latest_success_for_device(device_id)
        if latest is None or latest.finished_at is None:
            return True
        last = as_aware_utc(latest.finished_at)
        elapsed = (moment - last).total_seconds()
        return elapsed >= profile.sync_interval_seconds

    # -- sync runs ------------------------------------------------------------

    def initial_sync(self, profile: DeviceProfile) -> SyncResult:
        """First full sync: read the whole device log into local storage."""
        return self._run(profile, source=SyncSource.HISTORICAL, mode="initial")

    def incremental_sync(self, profile: DeviceProfile) -> SyncResult:
        """Re-read the whole log and store only what is new."""
        return self._run(profile, source=SyncSource.HISTORICAL, mode="incremental")

    def manual_sync(
        self, profile: DeviceProfile, *, requester_role: Role | str | None = None
    ) -> SyncResult:
        """Operator-triggered sync from the GUI.

        ``requester_role`` enforces PHASE 07 roles (admin or office staff).
        ``None`` keeps the path for callers without an interactive identity
        (tests, headless); the GUI always passes the logged-in role.
        """
        if requester_role is not None:
            require(requester_role, Permission.SYNC_ATTENDANCE)
        return self._run(profile, source=SyncSource.MANUAL, mode="manual")

    def background_sync(self, profile: DeviceProfile) -> SyncResult:
        """Periodic automatic sync. Same read, labelled by its source."""
        return self._run(profile, source=SyncSource.BACKGROUND, mode="background")

    def background_sync_if_due(self, profile: DeviceProfile) -> SyncResult | None:
        """Run a background sync only when the profile's interval has elapsed."""
        if not self.is_due(profile):
            return None
        return self.background_sync(profile)

    def recover(self, profile: DeviceProfile) -> SyncResult:
        """First sync after an offline period: re-read everything missed."""
        return self._run(profile, source=SyncSource.RECOVERY, mode="recovery")

    def record_live_event(
        self,
        profile: DeviceProfile,
        event: AttendanceEvent,
        *,
        requester_role: Role | str | None = None,
    ) -> bool:
        """Store one live-capture punch. Returns ``True`` when it was new.

        Live punches never raise for duplicates: a punch already picked up by
        a full sync is simply skipped. Unknown user IDs are stored with no
        employee snapshot rather than dropped.
        """
        return self.record_live_events(profile, [event], requester_role=requester_role) == 1

    def record_live_events(
        self,
        profile: DeviceProfile,
        events: list[AttendanceEvent],
        *,
        users_by_id: dict[str, str] | None = None,
        requester_role: Role | str | None = None,
    ) -> int:
        """Store live-capture punches, returning how many were new."""
        if requester_role is not None:
            require(requester_role, Permission.LIVE_CAPTURE)
        device_id = self._require_device_id(profile)
        if not events:
            return 0
        received_at = utc_now()
        names = users_by_id if users_by_id is not None else self._snapshot_names(profile, device_id)
        with self._database.session() as session:
            attendance = AttendanceRepository(session)
            known_keys = attendance.keys_for_device(device_id)
            known_natural = attendance.natural_keys_for_device(device_id)
            plan = plan_inserts(
                device_id=device_id,
                events=list(events),
                known_keys=known_keys,
                known_natural_keys=known_natural,
                users_by_id=names,
                source=SyncSource.LIVE,
            )
            inserted = 0
            for planned in plan.to_insert:
                if attendance.try_insert(
                    device_id=device_id,
                    device_uid=planned.event.device_uid,
                    user_id=planned.event.user_id,
                    occurred_at=planned.event.occurred_at,
                    punch=planned.event.punch,
                    status=planned.event.status,
                    received_at=received_at,
                    source=SyncSource.LIVE.value,
                    event_key=planned.event_key,
                    employee_name=planned.employee_name,
                ):
                    inserted += 1
        _logger.info(
            "Stored live attendance events",
            extra={"device": profile.name, "seen": len(events), "new": inserted},
        )
        return inserted

    def _run(self, profile: DeviceProfile, *, source: SyncSource, mode: str) -> SyncResult:
        device_id = self._require_device_id(profile)
        was_failing = self._was_failing(device_id)
        try:
            events, names = self._read_device(profile)
        except DeviceError as exc:
            result = SyncResult(
                ok=False,
                device_id=device_id,
                device_name=profile.name,
                source=source.value,
                is_recovery=False,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._record_history(profile, device_id, mode, source, 0, 0, 0, "failed", str(exc))
            _logger.warning(
                "Attendance sync failed",
                extra={"device": profile.name, "mode": mode, "error": str(exc)},
            )
            return result
        except ClockManagerError as exc:
            result = SyncResult(
                ok=False,
                device_id=device_id,
                device_name=profile.name,
                source=source.value,
                is_recovery=False,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._record_history(profile, device_id, mode, source, 0, 0, 0, "failed", str(exc))
            return result

        received_at = utc_now()
        with self._database.session() as session:
            attendance = AttendanceRepository(session)
            known_keys = attendance.keys_for_device(device_id)
            known_natural = attendance.natural_keys_for_device(device_id)
            plan = plan_inserts(
                device_id=device_id,
                events=events,
                known_keys=known_keys,
                known_natural_keys=known_natural,
                users_by_id=names,
                source=source,
            )
            inserted = 0
            for planned in plan.to_insert:
                if attendance.try_insert(
                    device_id=device_id,
                    device_uid=planned.event.device_uid,
                    user_id=planned.event.user_id,
                    occurred_at=planned.event.occurred_at,
                    punch=planned.event.punch,
                    status=planned.event.status,
                    received_at=received_at,
                    source=source.value,
                    event_key=planned.event_key,
                    employee_name=planned.employee_name,
                ):
                    inserted += 1
            duplicates = plan.counts.seen - inserted

        effective_source = source
        is_recovery = was_failing and source != SyncSource.RECOVERY
        if is_recovery:
            effective_source = SyncSource.RECOVERY
        self._devices.mark_seen(profile)
        self._record_history(
            profile,
            device_id,
            mode,
            effective_source,
            len(events),
            inserted,
            duplicates,
            "success",
            "",
        )
        _logger.info(
            "Attendance sync completed",
            extra={
                "device": profile.name,
                "mode": mode,
                "seen": len(events),
                "new": inserted,
                "duplicate": duplicates,
            },
        )
        return SyncResult(
            ok=True,
            device_id=device_id,
            device_name=profile.name,
            source=effective_source.value,
            seen=len(events),
            new=inserted,
            duplicate=duplicates,
            is_recovery=is_recovery,
        )

    def _read_device(self, profile: DeviceProfile) -> tuple[list[AttendanceEvent], dict[str, str]]:
        """Read attendance plus the user snapshot, always disconnecting."""
        with self._devices.connected(profile) as device:
            try:
                users = device.get_users()
            except DeviceError:
                # A punch without a name is still evidence. Store it with no
                # employee snapshot rather than failing the whole sync.
                _logger.warning(
                    "Sync could not read the user list; storing punches without names",
                    extra={"device": profile.name},
                )
                users = []
            names = {user.user_id: user.display_name for user in users}
            events = device.get_attendance()
        return events, names

    def _snapshot_names(self, profile: DeviceProfile, device_id: int) -> dict[str, str]:
        """Best-effort display-name snapshot for live events.

        Live punches arrive without a user list; a short device read supplies
        names when the clock is reachable. When it is not, the punches are
        still stored (offline recovery fills the names in on the next full
        sync) rather than dropped.
        """
        try:
            with self._devices.connected(profile) as device:
                users = device.get_users()
        except (DeviceError, ClockManagerError):
            return {}
        return {user.user_id: user.display_name for user in users}

    def _was_failing(self, device_id: int) -> bool:
        with self._database.session() as session:
            latest = SyncHistoryRepository(session).latest_for_device(device_id)
            return latest is not None and latest.outcome == "failed"

    def _record_history(
        self,
        profile: DeviceProfile,
        device_id: int,
        mode: str,
        source: SyncSource,
        seen: int,
        new: int,
        duplicate: int,
        outcome: str,
        error: str,
    ) -> None:
        with self._database.session() as session:
            SyncHistoryRepository(session).record_run(
                device_id=device_id,
                device_name=profile.name,
                mode=mode if mode != "initial" else SyncSource.HISTORICAL.value,
                source=source.value,
                events_seen=seen,
                events_new=new,
                events_duplicate=duplicate,
                outcome=outcome,
                error=error,
            )

    def _require_device_id(self, profile: DeviceProfile) -> int:
        if profile.device_id is None:
            raise ClockManagerError(
                "Save the device profile before syncing: attendance must be "
                "attributed to a stored device."
            )
        with self._database.session() as session:
            record = session.get(DeviceRecord, profile.device_id)
            if record is None:
                raise ClockManagerError(
                    "The stored device for this profile no longer exists. "
                    "Save the profile again before syncing."
                )
            return record.id

    @staticmethod
    def _to_stored(row: object) -> StoredAttendance:
        from clockmanager.persistence.models import AttendanceEventRecord as _Row

        assert isinstance(row, _Row)
        received = row.received_at
        if received is not None and received.tzinfo is None:
            received = received.replace(tzinfo=UTC)
        return StoredAttendance(
            record_id=row.id,
            device_id=row.device_id,
            device_uid=row.device_uid,
            user_id=row.user_id,
            employee_name=row.employee_name,
            occurred_at=row.occurred_at,
            punch=row.punch,
            status=row.status,
            received_at=received,
            source=row.source,
            event_key=row.event_key,
        )
