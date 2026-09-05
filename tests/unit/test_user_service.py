"""User management service tests.

These cover the policy layer: what is refused, what is confirmed, and what is
audited. The byte-level write sequence is covered in
``tests/unit/test_mb1_writes.py``; here the mock device stands in for it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from clockmanager.domain.users import CredentialAction, UserDraft
from clockmanager.errors import ClockManagerError
from clockmanager.protocol.errors import DeviceCapabilityError, DeviceWriteError
from clockmanager.protocol.interface import AttendanceDevice
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript
from clockmanager.protocol.records import parse_user_payload
from clockmanager.services.application import ApplicationContext
from clockmanager.services.audit import AuditAction, AuditOutcome, AuditService
from clockmanager.services.devices import DeviceProfile, DeviceService
from clockmanager.services.sample_data import sample_attendance
from clockmanager.services.users import UserService
from tests.fixtures.mb1 import sample_users


@pytest.fixture
def profile() -> DeviceProfile:
    return DeviceProfile(device_id=1, name="Bench clock", host="192.0.2.10")


class RecordingDeviceService(DeviceService):
    """A device service that always hands out the same mock device.

    Holding one device instance is what lets a test write through the service
    and then read the result back out of the same in-memory records.
    """

    def __init__(self, database: object, *, device: MockAttendanceDevice) -> None:
        self.device = device
        self.builds: list[tuple[bool, bool]] = []
        super().__init__(database, device_factory=self._factory)  # type: ignore[arg-type]

    def _factory(
        self,
        profile: DeviceProfile,
        *,
        allow_writes: bool = False,
        allow_credential_writes: bool = False,
    ) -> AttendanceDevice:
        self.builds.append((allow_writes, allow_credential_writes))
        return self.device


def build_mock(
    *,
    allow_writes: bool,
    allow_credential_writes: bool = False,
    write_failures: int = 0,
) -> MockAttendanceDevice:
    users = parse_user_payload(sample_users())
    return MockAttendanceDevice(
        script=MockDeviceScript(
            users=users,
            attendance=sample_attendance(users, days=2),
            write_failures=write_failures,
        ),
        allow_writes=allow_writes,
        allow_credential_writes=allow_credential_writes,
    )


@pytest.fixture
def locked(context: ApplicationContext) -> Iterator[UserService]:
    devices = RecordingDeviceService(context.database, device=build_mock(allow_writes=False))
    yield UserService(devices, AuditService(context.database, actor="tester"))


@pytest.fixture
def unlocked(context: ApplicationContext) -> Iterator[UserService]:
    devices = RecordingDeviceService(context.database, device=build_mock(allow_writes=True))
    yield UserService(
        devices,
        AuditService(context.database, actor="tester"),
        writes_enabled=True,
    )


@pytest.fixture
def fully_unlocked(context: ApplicationContext) -> Iterator[UserService]:
    devices = RecordingDeviceService(
        context.database,
        device=build_mock(allow_writes=True, allow_credential_writes=True),
    )
    yield UserService(
        devices,
        AuditService(context.database, actor="tester"),
        writes_enabled=True,
        credential_writes_enabled=True,
    )


class TestWriteAvailability:
    def test_writes_are_off_by_default(self, locked: UserService) -> None:
        """The write path is proven on hardware and still ships switched off.

        PHASE 15 graduated it from unproven to proven, which changes the
        wording but not the default: evidence that a device accepts a record
        is not permission for an installation to send one.
        """
        availability = locked.write_availability()
        assert not availability.users
        assert not availability.credentials
        assert "CLOCKMANAGER_ENABLE_DEVICE_WRITES" in availability.reason

    def test_enabling_writes_does_not_enable_credential_writes(self, unlocked: UserService) -> None:
        availability = unlocked.write_availability()
        assert availability.users
        assert not availability.credentials
        assert "CLOCKMANAGER_ENABLE_CREDENTIAL_WRITES" in availability.reason

    def test_credential_writes_require_user_writes(self, context: ApplicationContext) -> None:
        service = UserService(
            RecordingDeviceService(context.database, device=build_mock(allow_writes=False)),
            AuditService(context.database),
            writes_enabled=False,
            credential_writes_enabled=True,
        )
        assert not service.write_availability().credentials


class TestRefusals:
    def test_saving_is_refused_when_writes_are_off(
        self, locked: UserService, profile: DeviceProfile, context: ApplicationContext
    ) -> None:
        with pytest.raises(DeviceCapabilityError):
            locked.save_user(profile, UserDraft(user_id="NEW-1"))

        entries = context.audit.recent()
        assert entries[0].action == AuditAction.USER_CREATE.value
        assert entries[0].outcome == AuditOutcome.REFUSED.value

    def test_deleting_is_refused_when_writes_are_off(
        self, locked: UserService, profile: DeviceProfile, context: ApplicationContext
    ) -> None:
        with pytest.raises(DeviceCapabilityError):
            locked.delete_user(profile, 1, confirmed=True)

        assert context.audit.recent()[0].outcome == AuditOutcome.REFUSED.value

    def test_deleting_without_confirmation_sends_nothing(
        self, unlocked: UserService, profile: DeviceProfile, context: ApplicationContext
    ) -> None:
        """SECURITY.md: destructive operations require explicit confirmation."""
        with pytest.raises(ClockManagerError, match="explicit confirmation"):
            unlocked.delete_user(profile, 1, confirmed=False)

        assert unlocked.list_users(profile)  # nothing was removed
        assert context.audit.recent()[0].outcome == AuditOutcome.REFUSED.value

    def test_an_invalid_draft_is_refused_before_connecting(
        self, unlocked: UserService, profile: DeviceProfile, context: ApplicationContext
    ) -> None:
        with pytest.raises(ClockManagerError, match="User ID"):
            unlocked.save_user(profile, UserDraft(user_id="  "))

        assert context.audit.recent()[0].outcome == AuditOutcome.REFUSED.value

    def test_a_pin_change_is_refused_when_credential_writes_are_off(
        self, unlocked: UserService, profile: DeviceProfile
    ) -> None:
        with pytest.raises(DeviceCapabilityError):
            unlocked.save_user(
                profile,
                UserDraft(
                    user_id="1002",
                    device_uid=2,
                    credential_action=CredentialAction.SET,
                    password="1234",
                ),
            )


class TestSuccessfulWrites:
    def test_creates_a_user_and_audits_it(
        self, unlocked: UserService, profile: DeviceProfile, context: ApplicationContext
    ) -> None:
        outcome = unlocked.save_user(
            profile, UserDraft(user_id="EMP-900", first_name="Test", last_name="Account")
        )

        assert outcome.created
        assert outcome.user.user_id == "EMP-900"
        assert any(user.user_id == "EMP-900" for user in unlocked.list_users(profile))

        entry = context.audit.recent()[0]
        assert entry.action == AuditAction.USER_CREATE.value
        assert entry.outcome == AuditOutcome.SUCCEEDED.value
        assert entry.target == "EMP-900"
        assert entry.actor == "tester"
        assert entry.device_name == "Bench clock"

    def test_updates_a_user_and_records_what_changed(
        self, unlocked: UserService, profile: DeviceProfile, context: ApplicationContext
    ) -> None:
        outcome = unlocked.save_user(
            profile,
            UserDraft(user_id="1002", first_name="Grace", last_name="Hopper-Murray", device_uid=2),
        )

        assert not outcome.created
        entry = context.audit.recent()[0]
        assert entry.action == AuditAction.USER_UPDATE.value
        assert "Hopper-Murray" in entry.detail

    def test_deletes_a_user_and_audits_the_removal(
        self, unlocked: UserService, profile: DeviceProfile, context: ApplicationContext
    ) -> None:
        deleted = unlocked.delete_user(profile, 2, confirmed=True)

        assert deleted.user_id == "1002"
        assert all(user.device_uid != 2 for user in unlocked.list_users(profile))

        entry = context.audit.recent()[0]
        assert entry.action == AuditAction.USER_DELETE.value
        assert entry.outcome == AuditOutcome.SUCCEEDED.value
        assert "Attendance history on the device was not deleted" in entry.detail

    def test_a_pin_is_never_written_to_the_audit_log(
        self, fully_unlocked: UserService, profile: DeviceProfile, context: ApplicationContext
    ) -> None:
        """SECURITY.md: record that a PIN changed, never what it changed to."""
        fully_unlocked.save_user(
            profile,
            UserDraft(
                user_id="1002",
                first_name="Grace",
                last_name="Hopper",
                device_uid=2,
                credential_action=CredentialAction.SET,
                password="13579",
            ),
        )

        rendered = str([entry.as_row() for entry in context.audit.recent()])
        assert "13579" not in rendered
        assert "Set a new PIN" in rendered


class TestFailureAuditing:
    def test_a_device_failure_is_audited_and_re_raised(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        device = build_mock(allow_writes=True, write_failures=1)
        service = UserService(
            RecordingDeviceService(context.database, device=device),
            AuditService(context.database, actor="tester"),
            writes_enabled=True,
        )

        with pytest.raises(DeviceWriteError, match="rejected"):
            service.save_user(profile, UserDraft(user_id="EMP-901"))

        entry = context.audit.recent()[0]
        assert entry.outcome == AuditOutcome.FAILED.value


class TestDeleteImpact:
    def test_describes_the_exact_user_and_its_attendance(
        self, unlocked: UserService, profile: DeviceProfile
    ) -> None:
        impact = unlocked.describe_delete(profile, 2)

        assert impact.user.user_id == "1002"
        assert impact.attendance_records > 0
        assert "1002" in impact.warning
        assert "does NOT delete them" in impact.warning
        assert "cannot be undone" in impact.warning

    def test_warns_even_when_no_attendance_is_present(
        self, unlocked: UserService, profile: DeviceProfile
    ) -> None:
        created = unlocked.save_user(profile, UserDraft(user_id="EMP-902"))
        impact = unlocked.describe_delete(profile, created.user.device_uid)

        assert impact.attendance_records == 0
        assert "not deleted by this action" in impact.warning

    def test_refuses_to_describe_a_user_that_is_gone(
        self, unlocked: UserService, profile: DeviceProfile
    ) -> None:
        with pytest.raises(ClockManagerError, match="Refresh the user list"):
            unlocked.describe_delete(profile, 999)

    def test_impact_rows_show_only_pin_presence(
        self, unlocked: UserService, profile: DeviceProfile
    ) -> None:
        rows = dict(unlocked.describe_delete(profile, 1).as_rows())
        assert rows["PIN set"] in {"Yes", "No"}


def test_writes_are_only_requested_when_they_are_needed(
    context: ApplicationContext, profile: DeviceProfile
) -> None:
    """A read must not build a device that is allowed to write."""
    devices = RecordingDeviceService(context.database, device=build_mock(allow_writes=True))
    service = UserService(devices, AuditService(context.database), writes_enabled=True)

    service.list_users(profile)
    assert devices.builds == [(False, False)]

    service.save_user(profile, UserDraft(user_id="EMP-903"))
    assert devices.builds[-1] == (True, False)
