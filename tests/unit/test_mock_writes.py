"""Mock device write tests.

``TESTING.md`` requires the mock to cover writes, deletes and failures. It has
to enforce the same gates as the real adapter, otherwise tests written against
it would pass for code that a real device would refuse.
"""

from __future__ import annotations

import pytest

from clockmanager.domain.users import CredentialAction, UserDraft
from clockmanager.protocol.capabilities import Capability, Support
from clockmanager.protocol.errors import (
    DeviceCapabilityError,
    DeviceValidationError,
    DeviceVerificationError,
    DeviceWriteError,
)
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript
from clockmanager.protocol.records import parse_user_payload
from tests.fixtures.mb1 import sample_users


def build(**kwargs: object) -> MockAttendanceDevice:
    users = parse_user_payload(sample_users())
    script = MockDeviceScript(
        users=users,
        write_failures=int(kwargs.pop("write_failures", 0)),  # type: ignore[arg-type]
        corrupt_next_write=bool(kwargs.pop("corrupt_next_write", False)),
    )
    device = MockAttendanceDevice(script=script, **kwargs)  # type: ignore[arg-type]
    device.connect()
    return device


class TestGates:
    def test_writes_are_locked_by_default(self) -> None:
        device = build()
        assert device.capabilities.state(Capability.WRITE_USERS).support is Support.UNVERIFIED
        with pytest.raises(DeviceCapabilityError):
            device.apply_user_write(UserDraft(user_id="NEW-1"))

    def test_unlocking_reports_operator_enabled(self) -> None:
        device = build(allow_writes=True)
        state = device.capabilities.state(Capability.WRITE_USERS)
        assert state.support is Support.OPERATOR_ENABLED
        assert not state.proven

    def test_credential_writes_need_their_own_unlock(self) -> None:
        device = build(allow_writes=True)
        with pytest.raises(DeviceCapabilityError):
            device.apply_user_write(
                UserDraft(
                    user_id="1002",
                    device_uid=2,
                    credential_action=CredentialAction.CLEAR,
                )
            )


class TestWrites:
    def test_creates_a_user_at_the_lowest_free_uid(self) -> None:
        device = build(allow_writes=True)
        outcome = device.apply_user_write(UserDraft(user_id="EMP-900", first_name="Test"))

        assert outcome.created
        assert outcome.user.device_uid == 4
        assert len(device.get_users()) == 4

    def test_updates_an_existing_user(self) -> None:
        device = build(allow_writes=True)
        outcome = device.apply_user_write(
            UserDraft(user_id="1002", first_name="Grace", last_name="Murray", device_uid=2)
        )

        assert not outcome.created
        assert outcome.user.last_name == "Murray"

    def test_preserves_the_credential_region_on_an_update(self) -> None:
        device = build(allow_writes=True)
        before = next(record for record in device.read_raw_user_records() if record.uid == 1)
        assert before.has_credential_data

        device.apply_user_write(
            UserDraft(
                user_id="1001",
                first_name="Augusta",
                last_name="Lovelace",
                privilege=14,
                device_uid=1,
            )
        )

        after = next(record for record in device.read_raw_user_records() if record.uid == 1)
        assert after.credential_region == before.credential_region

    def test_refuses_a_duplicate_user_id(self) -> None:
        device = build(allow_writes=True)
        with pytest.raises(DeviceValidationError, match="already used"):
            device.apply_user_write(UserDraft(user_id="1001"))

    def test_refuses_updating_a_uid_that_is_absent(self) -> None:
        device = build(allow_writes=True)
        with pytest.raises(DeviceValidationError):
            device.apply_user_write(UserDraft(user_id="1002", device_uid=99))

    def test_a_rejected_write_leaves_the_device_unchanged(self) -> None:
        device = build(allow_writes=True, write_failures=1)
        with pytest.raises(DeviceWriteError):
            device.apply_user_write(UserDraft(user_id="EMP-900"))
        assert len(device.get_users()) == 3

    def test_a_corrupted_store_fails_read_back_verification(self) -> None:
        device = build(allow_writes=True, corrupt_next_write=True)
        with pytest.raises(DeviceVerificationError, match="does not match"):
            device.apply_user_write(UserDraft(user_id="EMP-900", first_name="Test"))


class TestDeletes:
    def test_deletes_and_returns_the_removed_user(self) -> None:
        device = build(allow_writes=True)
        deleted = device.delete_user(2)

        assert deleted.user_id == "1002"
        assert all(user.device_uid != 2 for user in device.get_users())

    def test_refuses_a_uid_that_is_absent(self) -> None:
        device = build(allow_writes=True)
        with pytest.raises(DeviceValidationError):
            device.delete_user(99)

    def test_a_rejected_delete_leaves_the_user(self) -> None:
        device = build(allow_writes=True, write_failures=1)
        with pytest.raises(DeviceWriteError):
            device.delete_user(2)
        assert any(user.device_uid == 2 for user in device.get_users())

    def test_deleting_does_not_touch_attendance(self) -> None:
        users = parse_user_payload(sample_users())
        from clockmanager.services.sample_data import sample_attendance

        script = MockDeviceScript(users=users, attendance=sample_attendance(users, days=1))
        device = MockAttendanceDevice(script=script, allow_writes=True)
        device.connect()
        before = len(device.get_attendance())

        device.delete_user(2)

        assert len(device.get_attendance()) == before


def test_a_write_requires_a_connection() -> None:
    from clockmanager.protocol.errors import DeviceNotConnectedError

    device = MockAttendanceDevice(
        script=MockDeviceScript(users=parse_user_payload(sample_users())),
        allow_writes=True,
    )
    with pytest.raises(DeviceNotConnectedError):
        device.apply_user_write(UserDraft(user_id="EMP-900"))
