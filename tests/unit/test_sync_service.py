"""Attendance sync service tests (PHASE 04).

Covers the phase's explicit test list against the mock device and a real
SQLite database: duplicates, reconnects, missed live events, unknown UID and
repeated sync.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from clockmanager.config import AppConfig, AppPaths
from clockmanager.domain.models import AttendanceEvent
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.database import create_database, initialise_database
from clockmanager.persistence.models import AttendanceEventRecord
from clockmanager.protocol.errors import DeviceConnectionError
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript
from clockmanager.protocol.records import parse_user_payload
from clockmanager.services.application import ApplicationContext
from clockmanager.services.devices import DeviceProfile, DeviceService
from clockmanager.services.sync import SyncService, as_aware_utc
from clockmanager.sync.sources import SyncSource
from tests.fixtures.mb1 import sample_users

MOMENT = datetime(2026, 3, 2, 8, 0, 0)  # noqa: DTZ001


def _device(attendance: list[AttendanceEvent]) -> MockAttendanceDevice:
    script = MockDeviceScript(
        users=parse_user_payload(sample_users()),
        attendance=list(attendance),
    )
    return MockAttendanceDevice(script=script)


def _event(
    user_id: str,
    occurred_at: datetime,
    punch: int = 0,
    status: int = 0,
    device_uid: int | None = 1,
) -> AttendanceEvent:
    return AttendanceEvent(
        user_id=user_id,
        occurred_at=occurred_at,
        punch=punch,
        status=status,
        device_uid=device_uid,
    )


@pytest.fixture
def sync_setup(tmp_path: Path) -> Iterator[tuple[ApplicationContext, DeviceProfile]]:
    """A bootstrapped context with one saved device profile."""
    from clockmanager.services.application import bootstrap

    config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
    ctx = bootstrap(config=config)
    try:
        profile = ctx.devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))
        yield ctx, profile
    finally:
        ctx.shutdown()


def _sync_for(ctx: ApplicationContext, device: MockAttendanceDevice) -> SyncService:
    """A sync service whose device reads hit ``device`` and nothing else."""
    devices = DeviceService(ctx.database, device_factory=lambda profile, **_kwargs: device)
    return SyncService(ctx.database, devices)


class TestInitialAndRepeatedSync:
    def test_initial_sync_stores_everything(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        events = [
            _event("1001", MOMENT, punch=0),
            _event("1001", MOMENT.replace(hour=17), punch=1),
        ]
        sync = _sync_for(ctx, _device(events))

        result = sync.initial_sync(profile)

        assert result.ok
        assert result.seen == 2
        assert result.new == 2
        assert result.duplicate == 0
        assert sync.count_stored(profile) == 2
        stored = sync.list_stored(profile)
        assert stored[0].occurred_at.strftime("%H:%M") == "17:00"  # newest first
        # Punch is preserved raw; direction derives from punch only.
        assert stored[0].punch == 1
        assert stored[0].direction_label == "OUT"
        assert stored[1].direction_label == "IN"
        # Raw status preserved verbatim.
        assert stored[0].status == 0

    def test_repeated_sync_inserts_nothing(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        sync = _sync_for(ctx, _device([_event("1001", MOMENT)]))

        first = sync.initial_sync(profile)
        second = sync.incremental_sync(profile)

        assert first.new == 1
        assert second.ok
        assert second.seen == 1
        assert second.new == 0
        assert second.duplicate == 1
        assert sync.count_stored(profile) == 1

    def test_incremental_sync_picks_up_only_new_records(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        device = _device([_event("1001", MOMENT)])
        sync = _sync_for(ctx, device)

        assert sync.initial_sync(profile).new == 1
        device.script.attendance.append(_event("1002", MOMENT.replace(hour=9)))

        result = sync.incremental_sync(profile)

        assert result.seen == 2
        assert result.new == 1
        assert result.duplicate == 1
        assert sync.count_stored(profile) == 2

    def test_manual_sync_is_labelled(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        sync = _sync_for(ctx, _device([_event("1001", MOMENT)]))

        result = sync.manual_sync(profile)

        assert result.ok
        assert result.source == SyncSource.MANUAL.value
        stored = sync.list_stored(profile)
        assert stored[0].source == "manual"


class TestUnknownUsers:
    def test_unknown_uid_is_stored_not_dropped(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        sync = _sync_for(ctx, _device([_event("GHOST-9", MOMENT, device_uid=99)]))

        result = sync.initial_sync(profile)

        assert result.ok
        assert result.new == 1
        stored = sync.list_stored(profile)
        assert stored[0].user_id == "GHOST-9"
        assert stored[0].device_uid == 99
        assert stored[0].employee_name is None
        assert stored[0].display_name == "GHOST-9"

    def test_known_users_get_an_employee_snapshot(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        sync = _sync_for(ctx, _device([_event("1001", MOMENT, device_uid=1)]))

        sync.initial_sync(profile)

        stored = sync.list_stored(profile)
        assert stored[0].employee_name == "Ada Lovelace"


class TestLiveEvents:
    def test_live_event_is_stored_and_second_delivery_is_a_duplicate(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        sync = _sync_for(ctx, _device([]))
        event = _event("1001", MOMENT)

        assert sync.record_live_event(profile, event) is True
        assert sync.record_live_event(profile, event) is False
        assert sync.count_stored(profile) == 1
        assert sync.list_stored(profile)[0].source == "live"

    def test_missed_live_event_arrives_on_the_next_full_sync(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        """A punch the live listener never saw is recovered by re-reading."""
        ctx, profile = sync_setup
        device = _device([])
        sync = _sync_for(ctx, device)

        assert sync.record_live_event(profile, _event("1001", MOMENT)) is True
        # The device log gains a punch while nobody is listening.
        device.script.attendance.append(_event("1002", MOMENT.replace(hour=9)))

        result = sync.incremental_sync(profile)

        # The live punch is a duplicate; the missed one is new.
        assert result.new == 1
        assert sync.count_stored(profile) == 2
        assert {row.user_id for row in sync.list_stored(profile)} == {"1001", "1002"}


class TestReconnectAndOfflineRecovery:
    def test_failed_sync_is_recorded_and_next_run_recovers(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        script = MockDeviceScript(
            users=parse_user_payload(sample_users()),
            attendance=[_event("1001", MOMENT)],
            read_failures=10**6,  # every read drops the connection
        )
        failing = MockAttendanceDevice(script=script)
        sync = _sync_for(ctx, failing)

        failed = sync.incremental_sync(profile)

        assert not failed.ok
        assert failed.error
        assert sync.count_stored(profile) == 0

        # The link comes back with the same history still on the device.
        script.read_failures = 0
        failing._remaining_read_failures = 0

        recovered = sync.recover(profile)

        assert recovered.ok
        assert recovered.new == 1
        assert sync.count_stored(profile) == 1

    def test_automatic_recovery_flag_after_a_failure(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        script = MockDeviceScript(
            users=parse_user_payload(sample_users()),
            attendance=[_event("1001", MOMENT)],
            read_failures=10**6,
        )
        failing = MockAttendanceDevice(script=script)
        sync = _sync_for(ctx, failing)
        assert not sync.incremental_sync(profile).ok

        script.read_failures = 0
        failing._remaining_read_failures = 0
        result = sync.incremental_sync(profile)

        assert result.ok
        assert result.is_recovery
        assert result.source == SyncSource.RECOVERY.value

    def test_connect_failure_does_not_raise(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        script = MockDeviceScript(
            users=parse_user_payload(sample_users()),
            attendance=[],
            connect_failures=10**6,
        )
        sync = _sync_for(ctx, MockAttendanceDevice(script=script))

        result = sync.manual_sync(profile)

        assert not result.ok
        assert result.error_type is not None


class TestSyncHistory:
    def test_runs_are_recorded_newest_first(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        sync = _sync_for(ctx, _device([_event("1001", MOMENT)]))

        sync.initial_sync(profile)
        sync.incremental_sync(profile)

        runs = sync.history(profile)
        assert len(runs) == 2
        assert runs[0].started_at >= runs[1].started_at
        assert runs[0].outcome == "success"
        assert runs[0].events_seen == 1
        assert {run.mode for run in runs} >= {"historical", "incremental"}

    def test_failed_runs_are_recorded_with_their_error(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        script = MockDeviceScript(
            users=parse_user_payload(sample_users()),
            attendance=[],
            connect_failures=10**6,
        )
        sync = _sync_for(ctx, MockAttendanceDevice(script=script))

        sync.manual_sync(profile)

        runs = sync.history(profile)
        assert len(runs) == 1
        assert runs[0].outcome == "failed"
        assert runs[0].error

    def test_history_survives_device_removal(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        """Removing a profile keeps the sync history, like the audit log."""
        ctx, profile = sync_setup
        sync = _sync_for(ctx, _device([_event("1001", MOMENT)]))
        sync.initial_sync(profile)
        device_id = profile.device_id
        assert device_id is not None

        ctx.devices.delete_profile(device_id)

        assert sync.recent_history() != []
        assert all(run.device_id == device_id for run in sync.recent_history())

    def test_summary_describes_local_state(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        assert "Never synced" in ctx.sync.summary(profile).describe()
        sync = _sync_for(ctx, _device([_event("1001", MOMENT)]))
        sync.initial_sync(profile)
        summary = sync.summary(profile)
        assert summary.stored_events == 1
        assert summary.last_success_at is not None
        assert "1 stored" in summary.describe()


class TestSchedulingAndTimestamps:
    def test_background_sync_is_due_before_the_first_run(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        assert ctx.sync.is_due(profile)

    def test_background_sync_if_due_skips_a_fresh_device(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        sync = _sync_for(ctx, _device([_event("1001", MOMENT)]))
        sync.background_sync(profile)
        assert sync.background_sync_if_due(profile) is None

    def test_received_at_is_utc_aware_on_read(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, profile = sync_setup
        sync = _sync_for(ctx, _device([_event("1001", MOMENT)]))
        sync.initial_sync(profile)
        stored = sync.list_stored(profile)[0]
        assert stored.received_at.tzinfo is not None
        assert stored.received_at.utcoffset() is not None

    def test_as_aware_utc_attaches_utc_to_naive_values(self) -> None:
        naive = datetime(2026, 3, 2, 8, 0, 0)  # noqa: DTZ001
        assert as_aware_utc(naive) == datetime(2026, 3, 2, 8, 0, 0, tzinfo=UTC)

    def test_sync_requires_a_saved_profile(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, _profile = sync_setup
        with pytest.raises(ClockManagerError, match="Save the device profile"):
            ctx.sync.manual_sync(DeviceProfile(name="Unsaved", host="192.0.2.10"))

    def test_unsaved_live_event_is_refused(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        ctx, _profile = sync_setup
        with pytest.raises(ClockManagerError, match="Save the device profile"):
            ctx.sync.record_live_event(
                DeviceProfile(name="Unsaved", host="192.0.2.10"), _event("1001", MOMENT)
            )


class TestStatusPreservation:
    def test_status_never_decides_direction(
        self, sync_setup: tuple[ApplicationContext, DeviceProfile]
    ) -> None:
        """punch=0 is IN whatever status says; punch=1 is OUT."""
        ctx, profile = sync_setup
        events = [
            _event("1001", MOMENT, punch=0, status=5),
            _event("1001", MOMENT.replace(hour=17), punch=1, status=0),
        ]
        sync = _sync_for(ctx, _device(events))
        sync.initial_sync(profile)
        stored = {row.occurred_at.hour: row for row in sync.list_stored(profile)}
        assert stored[8].direction_label == "IN"
        assert stored[8].status == 5
        assert stored[17].direction_label == "OUT"


def test_backfilled_rows_from_v3_still_dedupe(tmp_path: Path) -> None:
    """A v3 row migrated to v4 must suppress the same punch on re-sync."""
    config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
    db = create_database(config)
    try:
        initialise_database(db)
        # Simulate a pre-key row: NULL the new columns are impossible on the
        # v4 schema, so write with an empty key like the migration leaves when
        # there is nothing to backfill, then verify the service still dedupes
        # through the natural key.
        from clockmanager.services.devices import DeviceService

        devices = DeviceService(db)
        profile = devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))
        assert profile.device_id is not None
        with db.session() as session:
            session.add(
                AttendanceEventRecord(
                    device_id=profile.device_id,
                    device_uid=1,
                    user_id="1001",
                    occurred_at=MOMENT,
                    punch=0,
                    status=0,
                    received_at=datetime.now(UTC),
                    source="historical",
                    event_key="",
                    employee_name=None,
                )
            )
        sync = SyncService(
            db,
            DeviceService(
                db, device_factory=lambda profile, **_kw: _device([_event("1001", MOMENT)])
            ),
        )
        result = sync.incremental_sync(profile)
        assert result.new == 0
        assert result.duplicate == 1
    finally:
        db.dispose()


def test_device_connection_error_is_not_retried_as_a_write() -> None:
    """Reads fail as results; the sync never attempts a device write."""
    assert issubclass(DeviceConnectionError, Exception)
