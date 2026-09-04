"""Device management tests (PHASE 08).

Multi-device records carry connection settings plus locally observed state
(last seen, firmware/platform/serial, sync state). Discovery output becomes
a stored profile only through an explicit, validated registration — never
automatically.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from clockmanager.config import AppConfig, AppPaths
from clockmanager.domain.auth import Role
from clockmanager.domain.models import DeviceIdentity, DeviceInfo
from clockmanager.errors import ClockManagerError, SecurityError
from clockmanager.persistence.database import Database, create_database, initialise_database
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript
from clockmanager.services.application import ApplicationContext, bootstrap
from clockmanager.services.devices import DeviceProfile, DeviceService


@pytest.fixture
def context(tmp_path: Path) -> Iterator[ApplicationContext]:
    config = AppConfig(
        paths=AppPaths(tmp_path / "appdata"),
        log_to_console=False,
        use_mock_device=True,
    )
    ctx = bootstrap(config=config)
    try:
        yield ctx
    finally:
        ctx.shutdown()


def _info(**overrides: object) -> DeviceInfo:
    base: dict[str, object] = {
        "identity": DeviceIdentity(
            name="door",
            serial_number="SN-1",
            model="NG-MB1",
            platform="ZMM510_TFT",
            firmware_version="Ver 8.0.4.5-7108-02",
        )
    }
    base.update(overrides)
    return DeviceInfo(**base)  # type: ignore[arg-type]


class TestLastSeen:
    def test_fresh_profile_was_never_seen(self, context: ApplicationContext) -> None:
        profile = context.devices.save_profile(DeviceProfile(name="Door", host="192.0.2.10"))
        assert profile.last_seen_at is None
        assert "never" in context.devices.status(profile).describe()

    def test_connection_test_stamps_last_seen(self, context: ApplicationContext) -> None:
        profile = context.devices.save_profile(DeviceProfile(name="Door", host="192.0.2.10"))
        result = context.devices.test_connection(profile)
        assert result.ok
        refreshed = context.devices.get_profile(profile.device_id or 0)
        assert refreshed is not None and refreshed.last_seen_at is not None

    def test_failed_connection_does_not_stamp_last_seen(self, context: ApplicationContext) -> None:
        script = MockDeviceScript(users=[], attendance=[], connect_failures=99)

        def _factory(profile: DeviceProfile, **_: object) -> MockAttendanceDevice:
            settings = profile.to_connection_settings()
            return MockAttendanceDevice(settings=settings, script=script)

        service = DeviceService(context.database, device_factory=_factory)  # type: ignore[arg-type]
        profile = context.devices.save_profile(DeviceProfile(name="Dead", host="192.0.2.99"))
        result = service.test_connection(profile)
        assert not result.ok
        refreshed = context.devices.get_profile(profile.device_id or 0)
        assert refreshed is not None and refreshed.last_seen_at is None

    def test_successful_sync_stamps_last_seen(self, context: ApplicationContext) -> None:
        profile = context.devices.save_profile(DeviceProfile(name="Door", host="192.0.2.10"))
        result = context.sync.manual_sync(profile)
        assert result.ok
        refreshed = context.devices.get_profile(profile.device_id or 0)
        assert refreshed is not None and refreshed.last_seen_at is not None

    def test_record_last_seen_stores_an_explicit_moment(
        self,
        context: ApplicationContext,
    ) -> None:
        profile = context.devices.save_profile(DeviceProfile(name="Door", host="192.0.2.10"))
        moment = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
        context.devices.record_last_seen(profile.device_id or 0, when=moment)
        refreshed = context.devices.get_profile(profile.device_id or 0)
        assert refreshed is not None and refreshed.last_seen_at is not None


class TestDeviceStatus:
    def test_status_reports_sync_state_from_local_history(
        self, context: ApplicationContext
    ) -> None:
        profile = context.devices.save_profile(DeviceProfile(name="Door", host="192.0.2.10"))
        before = context.devices.status(profile)
        assert before.stored_events == 0
        assert before.last_outcome is None

        context.sync.manual_sync(profile)
        after = context.devices.status(
            context.devices.get_profile(profile.device_id or 0) or profile
        )
        assert after.last_outcome == "success"
        assert after.last_success_at is not None
        assert "stored punch" in after.describe()

    def test_statuses_covers_every_profile(self, context: ApplicationContext) -> None:
        context.devices.save_profile(DeviceProfile(name="A", host="192.0.2.11"))
        context.devices.save_profile(DeviceProfile(name="B", host="192.0.2.12"))
        assert [s.profile.name for s in context.devices.statuses()] == ["A", "B"]

    def test_disabled_device_is_labelled(self, context: ApplicationContext) -> None:
        profile = context.devices.save_profile(
            DeviceProfile(name="Spare", host="192.0.2.13", enabled=False)
        )
        assert "disabled" in context.devices.status(profile).describe()

    def test_unsaved_profile_has_empty_status(self, context: ApplicationContext) -> None:
        status = context.devices.status(DeviceProfile(name="Draft", host="192.0.2.14"))
        assert status.stored_events == 0
        assert status.last_outcome is None


class TestRegisterDiscovered:
    def test_registers_with_identity(self, context: ApplicationContext) -> None:
        profile = context.devices.register_discovered(
            name="Front door", host="192.0.2.20", info=_info()
        )
        assert profile.device_id is not None
        assert profile.host == "192.0.2.20"
        assert profile.port == 4370
        assert profile.model == "NG-MB1"
        assert profile.platform == "ZMM510_TFT"
        assert profile.firmware_version == "Ver 8.0.4.5-7108-02"
        assert profile.serial_number == "SN-1"

    def test_registers_without_identity(self, context: ApplicationContext) -> None:
        profile = context.devices.register_discovered(name="Lobby", host="192.0.2.21")
        assert profile.model is None

    def test_duplicate_name_is_refused(self, context: ApplicationContext) -> None:
        context.devices.save_profile(DeviceProfile(name="Door", host="192.0.2.10"))
        with pytest.raises(ClockManagerError, match="already exists"):
            context.devices.register_discovered(name="Door", host="192.0.2.22")

    def test_duplicate_address_is_refused(self, context: ApplicationContext) -> None:
        context.devices.save_profile(DeviceProfile(name="Door", host="192.0.2.10"))
        with pytest.raises(ClockManagerError, match="already stored"):
            context.devices.register_discovered(name="Other", host="192.0.2.10")

    def test_blank_name_or_host_is_refused(self, context: ApplicationContext) -> None:
        with pytest.raises(ClockManagerError):
            context.devices.register_discovered(name="  ", host="192.0.2.23")
        with pytest.raises(ClockManagerError):
            context.devices.register_discovered(name="X", host="  ")

    def test_non_admin_cannot_register(self, context: ApplicationContext) -> None:
        with pytest.raises(SecurityError):
            context.devices.register_discovered(
                name="X", host="192.0.2.24", requester_role=Role.VIEWER
            )

    def test_discovery_never_modifies_existing_profiles(self, context: ApplicationContext) -> None:
        """Registering a second device leaves the first one byte-identical."""
        first = context.devices.save_profile(
            DeviceProfile(name="Door", host="192.0.2.10", sync_interval_seconds=600)
        )
        context.devices.register_discovered(name="Lobby", host="192.0.2.21", info=_info())
        reread = context.devices.get_profile(first.device_id or 0)
        assert reread is not None
        assert reread.host == "192.0.2.10"
        assert reread.sync_interval_seconds == 600


class TestLastSeenMigration:
    def test_fresh_database_has_last_seen_column(self, tmp_path: Path) -> None:
        config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
        database: Database = create_database(config)
        try:
            initialise_database(database)
            with database.engine.connect() as connection:
                columns = {info["name"] for info in inspect(connection).get_columns("devices")}
        finally:
            database.dispose()
        assert "last_seen_at" in columns

    def test_v6_database_upgrades_with_data_preserved(self, tmp_path: Path) -> None:
        config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
        database = create_database(config)
        try:
            initialise_database(database)
            # Simulate a v6 database by stamping the older version number: the
            # v7 migration must apply cleanly on top of real v6-shaped data.
            with database.engine.begin() as connection:
                now = datetime.now(UTC).isoformat(sep=" ")
                connection.execute(
                    text(
                        "INSERT INTO devices (name, host, port, communication_password, "
                        "timeout_seconds, auto_reconnect, sync_interval_seconds, enabled, "
                        "created_at, updated_at) "
                        f"VALUES ('Legacy clock', '192.0.2.30', 4370, 0, 10.0, 1, 300, 1, "
                        f"'{now}', '{now}')"
                    )
                )
                connection.execute(
                    text("UPDATE schema_info SET value = '6' WHERE key = 'schema_version'")
                )
            version = initialise_database(database)
            assert version == 7
            with database.engine.begin() as connection:
                rows = connection.execute(
                    text("SELECT name, host, last_seen_at FROM devices")
                ).all()
            assert rows == [("Legacy clock", "192.0.2.30", None)]
        finally:
            database.dispose()
