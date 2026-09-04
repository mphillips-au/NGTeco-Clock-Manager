"""Device application service tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from clockmanager.config import AppConfig, AppPaths
from clockmanager.domain.models import DeviceIdentity, DeviceInfo
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.database import Database, create_database, initialise_database
from clockmanager.protocol.capabilities import Capability
from clockmanager.protocol.errors import DeviceConnectionError
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript
from clockmanager.protocol.records import parse_user_payload
from clockmanager.services.devices import (
    ConnectionTestResult,
    DeviceProfile,
    DeviceService,
    build_mock_device,
)
from tests.fixtures.mb1 import sample_users


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
    db = create_database(config)
    initialise_database(db)
    try:
        yield db
    finally:
        db.dispose()


def _profile(**overrides: object) -> DeviceProfile:
    base: dict[str, object] = {"name": "Bench clock", "host": "192.0.2.10"}
    base.update(overrides)
    return DeviceProfile(**base)  # type: ignore[arg-type]


def _mock_factory(script: MockDeviceScript | None = None):  # type: ignore[no-untyped-def]
    resolved = (
        script if script is not None else MockDeviceScript(users=parse_user_payload(sample_users()))
    )

    def factory(profile: DeviceProfile) -> MockAttendanceDevice:
        return MockAttendanceDevice(settings=profile.to_connection_settings(), script=resolved)

    return factory


class TestDeviceProfile:
    def test_password_is_not_in_repr(self) -> None:
        """SECURITY.md: the communication password must never be displayed."""
        rendered = repr(_profile(communication_password=987654))
        assert "987654" not in rendered
        assert "192.0.2.10" in rendered

    def test_endpoint_never_includes_the_password(self) -> None:
        assert _profile(communication_password=1234).endpoint == "192.0.2.10:4370"

    def test_unconfigured_profile_reports_no_address(self) -> None:
        profile = DeviceProfile(name="Unconfigured")
        assert not profile.is_configured
        assert "no address" in profile.endpoint

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"name": " "}, "Device name is required."),
            ({"host": ""}, "IP address or hostname is required."),
            ({"port": 0}, "Port must be between 1 and 65535."),
            ({"port": 99999}, "Port must be between 1 and 65535."),
            ({"timeout_seconds": 0}, "Timeout must be greater than zero seconds."),
            ({"sync_interval_seconds": 5}, "Sync interval must be at least 30 seconds."),
            (
                {"communication_password": -1},
                "Communication password must not be negative.",
            ),
        ],
    )
    def test_validation_messages(self, overrides: dict[str, object], expected: str) -> None:
        assert expected in _profile(**overrides).validate()

    def test_valid_profile_has_no_problems(self) -> None:
        assert _profile().validate() == []

    def test_to_connection_settings_rejects_an_invalid_profile(self) -> None:
        with pytest.raises(ClockManagerError):
            _profile(host="").to_connection_settings()

    def test_to_connection_settings_carries_the_fields_through(self) -> None:
        settings = _profile(port=4371, timeout_seconds=7.5).to_connection_settings()
        assert settings.host == "192.0.2.10"
        assert settings.port == 4371
        assert settings.timeout_seconds == 7.5


class TestProfileStorage:
    def test_save_and_list(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        saved = service.save_profile(_profile())

        assert saved.device_id is not None
        profiles = service.list_profiles()
        assert [p.name for p in profiles] == ["Bench clock"]
        assert profiles[0].host == "192.0.2.10"

    def test_save_persists_every_phase_02_setting(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        service.save_profile(
            _profile(
                port=4371,
                communication_password=1234,
                timeout_seconds=15.0,
                auto_reconnect=False,
                sync_interval_seconds=600,
                enabled=False,
            )
        )

        stored = service.list_profiles()[0]
        assert stored.port == 4371
        assert stored.communication_password == 1234
        assert stored.timeout_seconds == 15.0
        assert stored.auto_reconnect is False
        assert stored.sync_interval_seconds == 600
        assert stored.enabled is False

    def test_update_an_existing_profile(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        saved = service.save_profile(_profile())
        updated = service.save_profile(
            DeviceProfile(device_id=saved.device_id, name="Renamed", host="192.0.2.20")
        )

        assert updated.device_id == saved.device_id
        profiles = service.list_profiles()
        assert len(profiles) == 1
        assert profiles[0].name == "Renamed"
        assert profiles[0].host == "192.0.2.20"

    def test_duplicate_name_is_rejected(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        service.save_profile(_profile())
        with pytest.raises(ClockManagerError, match="already exists"):
            service.save_profile(_profile(host="192.0.2.11"))

    def test_invalid_profile_is_not_saved(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        with pytest.raises(ClockManagerError):
            service.save_profile(_profile(host=""))
        assert service.list_profiles() == []

    def test_get_profile(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        saved = service.save_profile(_profile())
        assert saved.device_id is not None
        assert service.get_profile(saved.device_id) is not None
        assert service.get_profile(9999) is None

    def test_delete_profile(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        saved = service.save_profile(_profile())
        assert saved.device_id is not None

        service.delete_profile(saved.device_id)
        assert service.list_profiles() == []
        service.delete_profile(saved.device_id)  # idempotent

    def test_first_enabled_profile_skips_disabled_devices(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        service.save_profile(_profile(name="Disabled one", enabled=False))
        service.save_profile(_profile(name="Working one", host="192.0.2.11"))

        active = service.first_enabled_profile()
        assert active is not None
        assert active.name == "Working one"

    def test_first_enabled_profile_when_none_exist(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        assert service.first_enabled_profile() is None

    def test_record_identity_stores_what_the_device_reported(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        saved = service.save_profile(_profile())

        service.record_identity(
            saved,
            DeviceInfo(
                identity=DeviceIdentity(
                    name="NG-MB1",
                    serial_number="SER-1",
                    model="NG-MB1",
                    platform="ZMM510_TFT",
                    firmware_version="Ver 8.0.4.5-7108-02",
                )
            ),
        )

        stored = service.list_profiles()[0]
        assert stored.platform == "ZMM510_TFT"
        assert stored.firmware_version == "Ver 8.0.4.5-7108-02"
        assert stored.serial_number == "SER-1"


class TestDeviceOperations:
    def test_test_connection_success(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        result = service.test_connection(_profile())

        assert isinstance(result, ConnectionTestResult)
        assert result.ok
        assert "192.0.2.10:4370" in result.summary
        assert result.info is not None

    def test_test_connection_failure_is_a_result_not_an_exception(self, database: Database) -> None:
        script = MockDeviceScript(connect_failures=99)
        service = DeviceService(database, device_factory=_mock_factory(script))

        result = service.test_connection(_profile())
        assert not result.ok
        assert result.error_type == DeviceConnectionError.__name__
        assert result.info is None

    def test_test_connection_reports_an_invalid_profile(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        result = service.test_connection(_profile(host=""))
        assert not result.ok
        assert "IP address" in result.summary

    def test_read_users(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        users = service.read_users(_profile())
        assert [u.user_id for u in users] == ["1001", "1002", "EMP-003"]

    def test_read_attendance(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        assert service.read_attendance(_profile()) == []

    def test_read_device_info(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        info = service.read_device_info(_profile())
        assert info.identity.model == "NG-MB1"

    def test_reads_always_disconnect(self, database: Database) -> None:
        devices: list[MockAttendanceDevice] = []

        def factory(profile: DeviceProfile) -> MockAttendanceDevice:
            device = MockAttendanceDevice(
                settings=profile.to_connection_settings(),
                script=MockDeviceScript(users=parse_user_payload(sample_users())),
            )
            devices.append(device)
            return device

        service = DeviceService(database, device_factory=factory)
        service.read_users(_profile())

        assert len(devices) == 1
        assert not devices[0].is_connected
        assert devices[0].disconnect_calls == 1

    def test_reads_disconnect_even_when_the_device_fails(self, database: Database) -> None:
        devices: list[MockAttendanceDevice] = []

        def factory(profile: DeviceProfile) -> MockAttendanceDevice:
            device = MockAttendanceDevice(
                settings=profile.to_connection_settings(),
                script=MockDeviceScript(read_failures=99),
            )
            devices.append(device)
            return device

        service = DeviceService(database, device_factory=factory)
        with pytest.raises(DeviceConnectionError):
            service.read_users(_profile())

        assert devices[0].disconnect_calls == 1

    def test_capabilities_are_exposed(self, database: Database) -> None:
        service = DeviceService(database, device_factory=_mock_factory())
        capabilities = service.capabilities(_profile())
        assert capabilities.supports(Capability.READ_USERS)
        assert not capabilities.supports(Capability.WRITE_USERS)

    def test_service_exposes_no_device_write_operations(self) -> None:
        """PHASE 02 performs no device writes."""
        public = {name for name in dir(DeviceService) if not name.startswith("_")}
        assert not public & {"set_user", "write_user", "delete_user", "clear_attendance"}
        # delete_profile removes a LOCAL record only; nothing writes to a device.
        assert "delete_profile" in public


class TestMockFactory:
    def test_mock_device_factory_produces_sample_data(self) -> None:
        device = build_mock_device(_profile())
        device.connect()
        try:
            users = device.get_users()
            events = device.get_attendance()
        finally:
            device.disconnect()

        assert len(users) == 5
        assert events
        assert all(event.punch in (0, 1) for event in events)

    def test_sample_data_holds_no_credential_bytes(self) -> None:
        device = build_mock_device(_profile())
        device.connect()
        try:
            users = device.get_users()
        finally:
            device.disconnect()
        assert all(not user.has_credential_data for user in users)


# -- mock device factory (PHASE 03) -------------------------------------------


def test_mock_factory_devices_remember_what_was_written() -> None:
    """A mock that forgets each write cannot exercise the write path at all."""
    from clockmanager.domain.users import UserDraft
    from clockmanager.services.devices import MockDeviceFactory

    factory = MockDeviceFactory()
    profile = DeviceProfile(name="Bench clock", host="192.0.2.10")

    writer = factory(profile, allow_writes=True)
    writer.connect()
    writer.apply_user_write(UserDraft(user_id="ZZTEST-1", first_name="Test"))
    writer.disconnect()

    reader = factory(profile)
    reader.connect()
    assert any(user.user_id == "ZZTEST-1" for user in reader.get_users())
    reader.disconnect()


def test_mock_factory_does_not_leak_write_unlocks_into_a_read() -> None:
    """Handing back a reused device must not carry an earlier unlock with it."""
    from clockmanager.protocol.capabilities import Capability
    from clockmanager.services.devices import MockDeviceFactory

    factory = MockDeviceFactory()
    profile = DeviceProfile(name="Bench clock", host="192.0.2.10")

    assert factory(profile, allow_writes=True).capabilities.supports(Capability.WRITE_USERS)
    assert not factory(profile).capabilities.supports(Capability.WRITE_USERS)


def test_two_factories_do_not_share_state() -> None:
    """Two application contexts must never see each other's mock devices."""
    from clockmanager.domain.users import UserDraft
    from clockmanager.services.devices import MockDeviceFactory

    profile = DeviceProfile(name="Bench clock", host="192.0.2.10")

    first = MockDeviceFactory()(profile, allow_writes=True)
    first.connect()
    first.apply_user_write(UserDraft(user_id="ZZTEST-1"))

    second = MockDeviceFactory()(profile)
    second.connect()
    assert all(user.user_id != "ZZTEST-1" for user in second.get_users())
