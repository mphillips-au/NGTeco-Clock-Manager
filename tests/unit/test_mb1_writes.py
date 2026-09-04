"""Adapter write-path tests.

A fake transport stands in for ``pyzk.ZK``, so the whole write sequence --
read, validate, build, send, verify the acknowledgement, read back, compare --
is exercised without a socket and without a real clock.

The fake records exactly which command codes and payloads it received, which is
how these tests prove the adapter sends a 120-byte MB1 record and never the
generic 72-byte packet.
"""

from __future__ import annotations

from struct import unpack
from typing import Any

import pytest

from clockmanager.domain.users import CredentialAction, UserDraft
from clockmanager.protocol.builders import parse_raw_user_records
from clockmanager.protocol.constants import (
    ADMIN_PRIVILEGE,
    CMD_DELETE_USER,
    CMD_REFRESHDATA,
    CMD_USER_WRQ,
    CMD_USERTEMP_RRQ,
    EMPLOYEE_PRIVILEGE,
    MB1_USER_RECORD_SIZE,
    PYZK_USER_PACKET_SIZE,
    USER_CREDENTIAL_SLICE,
)
from clockmanager.protocol.errors import (
    DeviceCapabilityError,
    DeviceValidationError,
    DeviceVerificationError,
    DeviceWriteError,
)
from clockmanager.protocol.interface import DeviceConnectionSettings
from clockmanager.protocol.mb1 import NGTecoMB1Device
from clockmanager.protocol.retry import RetryPolicy
from tests.fixtures.mb1 import build_user_payload, build_user_record


def settings() -> DeviceConnectionSettings:
    return DeviceConnectionSettings(name="Bench clock", host="192.0.2.10", timeout_seconds=1.0)


class WritableFakeTransport:
    """A ``pyzk.ZK`` stand-in that stores 120-byte records in memory."""

    tcp = True

    def __init__(self, records: list[bytes] | None = None) -> None:
        self.records: dict[int, bytes] = {}
        for record in records or []:
            self.records[unpack("<H", record[:2])[0]] = record
        self.sent: list[tuple[int, bytes]] = []
        #: Response codes to return instead of success, consumed in order.
        self.reject_next = 0
        #: Store a mangled record instead of the one sent, once.
        self.corrupt_next = False
        self.refresh_calls = 0

    # -- pyzk surface ---------------------------------------------------------

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def get_device_name(self) -> str:
        return "Mock NG-MB1"

    def get_serialnumber(self) -> str:
        return "MOCK-0000000001"

    def get_platform(self) -> str:
        return "ZMM510_TFT"

    def get_firmware_version(self) -> str:
        return "Ver 8.0.4.5-7108-02"

    def get_time(self) -> Any:
        from datetime import datetime

        return datetime(2026, 3, 1, 9, 0, 0)  # noqa: DTZ001

    def read_sizes(self) -> None:
        self.users = len(self.records)
        self.records_count = 0

    def read_with_buffer(self, command: int, *_args: Any) -> tuple[bytes, int]:
        assert command == CMD_USERTEMP_RRQ
        payload = build_user_payload([self.records[uid] for uid in sorted(self.records)])
        return payload, len(payload)

    def _ZK__send_command(  # noqa: N802 - mirrors pyzk's name mangling
        self, command: int, command_string: bytes = b"", response_size: int = 8
    ) -> dict[str, Any]:
        self.sent.append((command, command_string))

        if command == CMD_REFRESHDATA:
            self.refresh_calls += 1
            return {"status": True, "code": 2000}

        if self.reject_next > 0:
            self.reject_next -= 1
            return {"status": False, "code": 65533}

        if command == CMD_USER_WRQ:
            record = command_string
            if self.corrupt_next:
                self.corrupt_next = False
                record = record[:35] + b"Z" + record[36:]
            self.records[unpack("<H", record[:2])[0]] = record
            return {"status": True, "code": 2000}

        if command == CMD_DELETE_USER:
            self.records.pop(unpack("<H", command_string)[0], None)
            return {"status": True, "code": 2000}

        return {"status": True, "code": 2000}  # pragma: no cover - unused

    # -- helpers --------------------------------------------------------------

    @property
    def written_records(self) -> list[bytes]:
        return [payload for command, payload in self.sent if command == CMD_USER_WRQ]


def build_device(transport: WritableFakeTransport, **kwargs: Any) -> NGTecoMB1Device:
    kwargs.setdefault("allow_writes", True)
    kwargs.setdefault("retry_policy", RetryPolicy(attempts=1, initial_backoff_seconds=0))
    device = NGTecoMB1Device(settings(), transport_factory=lambda _s: transport, **kwargs)
    device.connect()
    return device


def existing_records() -> list[bytes]:
    return [
        build_user_record(
            uid=1,
            user_id="1001",
            first_name="Ada",
            last_name="Lovelace",
            privilege=ADMIN_PRIVILEGE,
            with_credential=True,
        ),
        build_user_record(uid=2, user_id="1002", first_name="Grace", last_name="Hopper"),
    ]


class TestCapabilityGate:
    def test_writing_is_refused_until_unlocked(self) -> None:
        device = build_device(WritableFakeTransport(existing_records()), allow_writes=False)
        with pytest.raises(DeviceCapabilityError, match="write_users"):
            device.apply_user_write(UserDraft(user_id="NEW-1"))

    def test_deleting_is_refused_until_unlocked(self) -> None:
        device = build_device(WritableFakeTransport(existing_records()), allow_writes=False)
        with pytest.raises(DeviceCapabilityError, match="delete_users"):
            device.delete_user(1)

    def test_nothing_is_sent_when_a_write_is_refused(self) -> None:
        transport = WritableFakeTransport(existing_records())
        device = build_device(transport, allow_writes=False)
        with pytest.raises(DeviceCapabilityError):
            device.apply_user_write(UserDraft(user_id="NEW-1"))
        assert transport.sent == []

    def test_credential_writing_needs_its_own_unlock(self) -> None:
        """Unlocking user writes must not silently unlock PIN writes."""
        device = build_device(WritableFakeTransport(existing_records()))
        with pytest.raises(DeviceCapabilityError, match="write_user_password"):
            device.apply_user_write(
                UserDraft(
                    user_id="1002",
                    device_uid=2,
                    credential_action=CredentialAction.SET,
                    password="1234",
                )
            )

    def test_credential_unlock_requires_the_write_unlock(self) -> None:
        with pytest.raises(DeviceValidationError, match="user writing is locked"):
            NGTecoMB1Device(
                settings(),
                transport_factory=lambda _s: WritableFakeTransport(),
                allow_writes=False,
                allow_credential_writes=True,
            )


class TestCreate:
    def test_sends_exactly_one_120_byte_record(self) -> None:
        transport = WritableFakeTransport(existing_records())
        device = build_device(transport)

        device.apply_user_write(UserDraft(user_id="EMP-003", first_name="Alan", last_name="Turing"))

        assert len(transport.written_records) == 1
        assert len(transport.written_records[0]) == MB1_USER_RECORD_SIZE
        assert len(transport.written_records[0]) != PYZK_USER_PACKET_SIZE

    def test_assigns_the_lowest_free_uid(self) -> None:
        transport = WritableFakeTransport(existing_records())
        device = build_device(transport)

        outcome = device.apply_user_write(UserDraft(user_id="EMP-003"))

        assert outcome.created
        assert outcome.user.device_uid == 3
        assert outcome.user.user_id == "EMP-003"

    def test_refuses_a_duplicate_user_id(self) -> None:
        transport = WritableFakeTransport(existing_records())
        device = build_device(transport)

        with pytest.raises(DeviceValidationError, match="already used"):
            device.apply_user_write(UserDraft(user_id="1001"))
        assert transport.written_records == []

    def test_refreshes_the_device_after_writing(self) -> None:
        """Without CMD_REFRESHDATA a read-back can return pre-write state."""
        transport = WritableFakeTransport(existing_records())
        build_device(transport).apply_user_write(UserDraft(user_id="EMP-003"))
        assert transport.refresh_calls == 1


class TestUpdate:
    def test_updates_an_existing_record_in_place(self) -> None:
        transport = WritableFakeTransport(existing_records())
        device = build_device(transport)

        outcome = device.apply_user_write(
            UserDraft(
                user_id="1002",
                first_name="Grace",
                last_name="Hopper-Murray",
                device_uid=2,
            )
        )

        assert not outcome.created
        assert outcome.user.last_name == "Hopper-Murray"
        assert len(transport.records) == 2

    def test_preserves_the_credential_region_on_a_name_change(self) -> None:
        """The point of read-modify-write: renaming must not clear a PIN."""
        records = existing_records()
        transport = WritableFakeTransport(records)
        original_region = records[0][USER_CREDENTIAL_SLICE]

        build_device(transport).apply_user_write(
            UserDraft(user_id="1001", first_name="Augusta", last_name="Lovelace", device_uid=1)
        )

        assert transport.records[1][USER_CREDENTIAL_SLICE] == original_region

    def test_reports_the_changes_it_made(self) -> None:
        device = build_device(WritableFakeTransport(existing_records()))
        outcome = device.apply_user_write(
            UserDraft(
                user_id="1002",
                first_name="Grace",
                last_name="Hopper",
                privilege=ADMIN_PRIVILEGE,
                device_uid=2,
            )
        )
        rendered = [str(change) for change in outcome.changes]
        assert any("Privilege" in change and "Admin" in change for change in rendered)

    def test_refuses_an_update_to_a_uid_that_is_not_there(self) -> None:
        transport = WritableFakeTransport(existing_records())
        device = build_device(transport)

        with pytest.raises(DeviceValidationError, match="Re-read the user list"):
            device.apply_user_write(UserDraft(user_id="1002", device_uid=99))
        assert transport.written_records == []

    def test_refuses_taking_a_user_id_from_another_user(self) -> None:
        device = build_device(WritableFakeTransport(existing_records()))
        with pytest.raises(DeviceValidationError, match="already used"):
            device.apply_user_write(UserDraft(user_id="1001", device_uid=2))

    def test_refuses_an_unverified_privilege(self) -> None:
        device = build_device(WritableFakeTransport(existing_records()))
        with pytest.raises(DeviceValidationError, match="verified"):
            device.apply_user_write(UserDraft(user_id="1002", device_uid=2, privilege=3))


class TestCredentialWrites:
    def _device(self) -> tuple[WritableFakeTransport, NGTecoMB1Device]:
        transport = WritableFakeTransport(existing_records())
        return transport, build_device(transport, allow_credential_writes=True)

    def test_setting_a_pin_writes_the_candidate_field(self) -> None:
        transport, device = self._device()
        device.apply_user_write(
            UserDraft(
                user_id="1002",
                first_name="Grace",
                last_name="Hopper",
                device_uid=2,
                credential_action=CredentialAction.SET,
                password="9876",
            )
        )
        assert transport.records[2][USER_CREDENTIAL_SLICE][:8] == b"9876\x00\x00\x00\x00"

    def test_clearing_a_pin_zeroes_the_region(self) -> None:
        transport, device = self._device()
        device.apply_user_write(
            UserDraft(
                user_id="1001",
                first_name="Ada",
                last_name="Lovelace",
                privilege=ADMIN_PRIVILEGE,
                device_uid=1,
                credential_action=CredentialAction.CLEAR,
            )
        )
        assert not any(transport.records[1][USER_CREDENTIAL_SLICE])

    def test_the_pin_never_appears_in_the_outcome(self) -> None:
        """SECURITY.md: the value must not travel back out of the write."""
        _transport, device = self._device()
        outcome = device.apply_user_write(
            UserDraft(
                user_id="1002",
                first_name="Grace",
                last_name="Hopper",
                device_uid=2,
                credential_action=CredentialAction.SET,
                password="9876",
            )
        )
        assert "9876" not in repr(outcome)
        assert "9876" not in "".join(str(change) for change in outcome.changes)


class TestFailureHandling:
    def test_a_rejected_write_raises_and_is_not_retried(self) -> None:
        """A retry could apply the same change twice."""
        transport = WritableFakeTransport(existing_records())
        transport.reject_next = 1
        device = build_device(transport)

        with pytest.raises(DeviceWriteError, match="rejected"):
            device.apply_user_write(UserDraft(user_id="EMP-003"))

        assert len(transport.written_records) == 1

    def test_a_record_stored_differently_fails_verification(self) -> None:
        """Read-back is the point: an acknowledged write can still be wrong."""
        transport = WritableFakeTransport(existing_records())
        transport.corrupt_next = True
        device = build_device(transport)

        with pytest.raises(DeviceVerificationError, match="does not match"):
            device.apply_user_write(UserDraft(user_id="EMP-003", first_name="Alan"))

    def test_a_draft_that_fails_validation_never_reaches_the_wire(self) -> None:
        transport = WritableFakeTransport(existing_records())
        device = build_device(transport)

        with pytest.raises(DeviceValidationError):
            device.apply_user_write(UserDraft(user_id="A" * 40))
        assert transport.sent == []


class TestDelete:
    def test_deletes_and_verifies_the_removal(self) -> None:
        transport = WritableFakeTransport(existing_records())
        device = build_device(transport)

        deleted = device.delete_user(2)

        assert deleted.user_id == "1002"
        assert deleted.display_name == "Grace Hopper"
        assert 2 not in transport.records

    def test_sends_the_uid_as_a_two_byte_payload(self) -> None:
        transport = WritableFakeTransport(existing_records())
        build_device(transport).delete_user(2)

        payloads = [payload for command, payload in transport.sent if command == CMD_DELETE_USER]
        assert payloads == [b"\x02\x00"]

    def test_refuses_a_uid_that_is_not_on_the_device(self) -> None:
        transport = WritableFakeTransport(existing_records())
        device = build_device(transport)

        with pytest.raises(DeviceValidationError, match="Re-read the user list"):
            device.delete_user(99)
        assert transport.sent == [(CMD_USERTEMP_RRQ, b"")] or not [
            command for command, _ in transport.sent if command == CMD_DELETE_USER
        ]

    def test_a_rejected_delete_raises_and_leaves_the_user(self) -> None:
        transport = WritableFakeTransport(existing_records())
        transport.reject_next = 1
        device = build_device(transport)

        with pytest.raises(DeviceWriteError):
            device.delete_user(2)
        assert 2 in transport.records

    def test_does_not_touch_attendance(self) -> None:
        """AGENTS.md: attendance is never cleared automatically."""
        transport = WritableFakeTransport(existing_records())
        build_device(transport).delete_user(2)

        commands = {command for command, _ in transport.sent}
        assert commands <= {CMD_DELETE_USER, CMD_REFRESHDATA}


class TestRawRecordsStayInTheProtocolLayer:
    def test_read_raw_user_records_returns_protocol_types(self) -> None:
        transport = WritableFakeTransport(existing_records())
        records = build_device(transport).read_raw_user_records()

        assert [record.uid for record in records] == [1, 2]
        assert records[0].has_credential_data

    def test_get_users_discards_the_credential_region(self) -> None:
        """The domain model has nowhere to put credential bytes."""
        transport = WritableFakeTransport(existing_records())
        users = build_device(transport).get_users()

        assert users[0].has_credential_data is True
        assert not hasattr(users[0], "raw")
        assert not hasattr(users[0], "credential_region")

    def test_the_written_record_matches_what_the_parser_reads_back(self) -> None:
        transport = WritableFakeTransport(existing_records())
        build_device(transport).apply_user_write(
            UserDraft(user_id="EMP-003", first_name="Alan", last_name="Turing")
        )

        payload, _size = transport.read_with_buffer(CMD_USERTEMP_RRQ)
        parsed = {record.uid: record for record in parse_raw_user_records(payload)}
        assert parsed[3].user_id == "EMP-003"
        assert parsed[3].raw == transport.written_records[0]


def test_next_available_uid_skips_taken_uids() -> None:
    transport = WritableFakeTransport(existing_records())
    assert build_device(transport).next_available_uid() == 3


def test_a_new_user_is_written_as_an_employee_by_default() -> None:
    transport = WritableFakeTransport(existing_records())
    outcome = build_device(transport).apply_user_write(UserDraft(user_id="EMP-003"))
    assert outcome.user.privilege == EMPLOYEE_PRIVILEGE
