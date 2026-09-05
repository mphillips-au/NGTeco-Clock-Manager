"""Tests pinning what PHASE 15 proved on the real NG-MB1.

Every assertion here corresponds to an observation recorded in ``PROTOCOL.md``
under "PHASE 15" and in ``phases/PHASE-15.md``. Where a behaviour was NOT
proven, there is deliberately no test asserting it — an untested guess pinned
by a passing test is worse than no test at all.
"""

from __future__ import annotations

from dataclasses import astuple

import pytest

from clockmanager.domain.models import FingerprintSlot
from clockmanager.domain.users import (
    LAST_NAME_MAX_BYTES,
    USER_ID_MAX_BYTES,
    CredentialAction,
    UserDraft,
)
from clockmanager.protocol.builders import build_user_record, parse_raw_user_records
from clockmanager.protocol.capabilities import (
    NG_MB1_CAPABILITIES,
    WRITE_CAPABILITIES,
    Capability,
    Support,
)
from clockmanager.protocol.constants import (
    EMPLOYEE_PRIVILEGE,
    USER_DEVICE_FLAG_OFFSETS,
    USER_ID_WRITABLE_BYTES,
    USER_LAST_NAME_WRITABLE_BYTES,
    USER_PASSWORD_SLICE,
)
from clockmanager.protocol.errors import (
    DeviceCapabilityError,
    DeviceParseError,
    DeviceValidationError,
    DeviceVerificationError,
)
from clockmanager.protocol.mb1 import NGTecoMB1Device
from clockmanager.protocol.records import parse_fingerprint_payload
from tests.fixtures.mb1 import (
    CREDENTIAL_MARKER_BYTE,
    build_fingerprint_entry,
    build_fingerprint_payload,
    build_user_payload,
)


class TestCredentialRegion:
    """The PIN field, proven on a disposable user (PROTOCOL.md, PHASE 15)."""

    def test_the_pin_is_eight_ascii_bytes_at_record_offset_three(self) -> None:
        record = build_user_record(
            uid=3,
            user_id="ZZT-1",
            privilege=EMPLOYEE_PRIVILEGE,
            credential_action=CredentialAction.SET,
            password="1234",
        )
        # Exactly what the device stored back on hardware.
        assert record[USER_PASSWORD_SLICE] == b"1234" + bytes(4)
        assert record[USER_PASSWORD_SLICE.stop : 35] == bytes(24)

    def test_clearing_zeroes_the_whole_region(self) -> None:
        record = build_user_record(
            uid=3,
            user_id="ZZT-1",
            privilege=EMPLOYEE_PRIVILEGE,
            credential_action=CredentialAction.CLEAR,
        )
        assert not any(record[3:35])

    def test_a_new_user_has_no_credential(self) -> None:
        """A user created with no PIN read back with an all-zero region."""
        record = build_user_record(uid=3, user_id="ZZT-1", privilege=EMPLOYEE_PRIVILEGE)
        assert not any(record[3:35])


class TestFieldBudgets:
    """The device keeps less than the record has room for."""

    def test_user_id_is_bounded_to_the_device_reported_pin_width(self) -> None:
        assert USER_ID_WRITABLE_BYTES == 9
        assert USER_ID_MAX_BYTES == USER_ID_WRITABLE_BYTES

    def test_last_name_is_bounded_to_what_the_device_keeps(self) -> None:
        assert USER_LAST_NAME_WRITABLE_BYTES == 23
        assert LAST_NAME_MAX_BYTES == USER_LAST_NAME_WRITABLE_BYTES

    def test_the_builder_refuses_an_over_long_user_id(self) -> None:
        """This exact packet made a record undeletable and wedged the clock."""
        with pytest.raises(DeviceValidationError, match="PIN2Width"):
            build_user_record(uid=901, user_id="ZZTEST-LONGID", privilege=EMPLOYEE_PRIVILEGE)

    def test_the_builder_refuses_an_over_long_last_name(self) -> None:
        with pytest.raises(DeviceValidationError, match="truncates"):
            build_user_record(
                uid=3, user_id="ZZT-1", last_name="L" * 30, privilege=EMPLOYEE_PRIVILEGE
            )

    def test_the_domain_draft_refuses_them_too(self) -> None:
        """The GUI must not be able to offer what the builder will reject."""
        problems = UserDraft(user_id="ZZTEST-LONGID", last_name="L" * 30).validate()
        assert any("User ID" in problem for problem in problems)
        assert any("Last name" in problem for problem in problems)

    def test_a_record_that_fits_is_still_exactly_120_bytes(self) -> None:
        record = build_user_record(
            uid=3,
            user_id="ZZT-1",
            first_name="Zz",
            last_name="L" * USER_LAST_NAME_WRITABLE_BYTES,
            privilege=EMPLOYEE_PRIVILEGE,
        )
        assert len(record) == 120


class TestReadBackComparison:
    """Verification compares meaning, not bytes (PROTOCOL.md, PHASE 15)."""

    @staticmethod
    def _device() -> NGTecoMB1Device:
        from clockmanager.protocol.interface import DeviceConnectionSettings

        return NGTecoMB1Device(DeviceConnectionSettings(name="t", host="192.0.2.10"))

    def test_a_device_owned_flag_byte_does_not_fail_a_write(self) -> None:
        """Byte 87 comes back 0x01 on every record whatever we send."""
        sent = build_user_record(
            uid=3,
            user_id="ZZT-1",
            first_name="Zz",
            last_name="Kept",
            privilege=EMPLOYEE_PRIVILEGE,
        )
        stored = bytearray(sent)
        for offset in USER_DEVICE_FLAG_OFFSETS:
            stored[offset] = 0x01

        self._device()._compare_records(sent=sent, stored=bytes(stored), uid=3)

    def test_residue_after_a_field_terminator_does_not_fail_a_write(self) -> None:
        """The device does not zero-fill; both real users carry such residue."""
        sent = build_user_record(
            uid=3,
            user_id="ZZT-1",
            first_name="Zz",
            last_name="Kept",
            privilege=EMPLOYEE_PRIVILEGE,
        )
        stored = bytearray(sent)
        stored[38] = ord("n")  # residue from a previous occupant of the slot
        stored[65:68] = b"nis"

        self._device()._compare_records(sent=sent, stored=bytes(stored), uid=3)

    def test_a_field_the_device_really_changed_still_fails(self) -> None:
        """Tolerating residue must not tolerate a wrong name."""
        sent = build_user_record(
            uid=3,
            user_id="ZZT-1",
            first_name="Zz",
            last_name="Kept",
            privilege=EMPLOYEE_PRIVILEGE,
        )
        stored = build_user_record(
            uid=3,
            user_id="ZZT-1",
            first_name="Zz",
            last_name="Different",
            privilege=EMPLOYEE_PRIVILEGE,
        )
        with pytest.raises(DeviceVerificationError, match="last name"):
            self._device()._compare_records(sent=sent, stored=stored, uid=3)

    def test_a_changed_privilege_still_fails(self) -> None:
        sent = build_user_record(uid=3, user_id="ZZT-1", privilege=0)
        stored = build_user_record(uid=3, user_id="ZZT-1", privilege=14)
        with pytest.raises(DeviceVerificationError, match="privilege"):
            self._device()._compare_records(sent=sent, stored=stored, uid=3)

    def test_a_lost_credential_still_fails(self) -> None:
        sent = build_user_record(
            uid=3,
            user_id="ZZT-1",
            privilege=EMPLOYEE_PRIVILEGE,
            credential_action=CredentialAction.SET,
            password="1234",
        )
        stored = build_user_record(
            uid=3,
            user_id="ZZT-1",
            privilege=EMPLOYEE_PRIVILEGE,
            credential_action=CredentialAction.CLEAR,
        )
        with pytest.raises(DeviceVerificationError, match="credential"):
            self._device()._compare_records(sent=sent, stored=stored, uid=3)


class TestFingerprintEnumeration:
    """Proven on hardware: entry framing and UID mapping, never templates."""

    def test_entries_are_enumerated_with_their_uid_and_length(self) -> None:
        payload = build_fingerprint_payload(
            [
                build_fingerprint_entry(uid=1, finger_index=6, template_bytes=838),
                build_fingerprint_entry(uid=2, finger_index=6, template_bytes=828),
            ]
        )
        slots = parse_fingerprint_payload(payload)

        assert slots == [
            FingerprintSlot(device_uid=1, finger_index=6, valid=1, template_bytes=838),
            FingerprintSlot(device_uid=2, finger_index=6, valid=1, template_bytes=828),
        ]
        assert all(slot.is_valid for slot in slots)

    def test_the_uids_match_the_user_records(self) -> None:
        """The mapping the whole feature depends on, as observed on hardware."""
        from tests.fixtures.mb1 import sample_users

        user_uids = {record.uid for record in parse_raw_user_records(sample_users())}
        payload = build_fingerprint_payload(
            [build_fingerprint_entry(uid=uid) for uid in sorted(user_uids)]
        )
        assert {slot.device_uid for slot in parse_fingerprint_payload(payload)} == user_uids

    def test_no_template_byte_is_returned(self) -> None:
        """SECURITY.md: template contents never leave the protocol layer."""
        marker = bytes([CREDENTIAL_MARKER_BYTE])
        payload = build_fingerprint_payload([build_fingerprint_entry(uid=1)])
        assert marker * 16 in payload, "the fixture must actually carry a body"

        slots = parse_fingerprint_payload(payload)

        # The slot carries only numbers, and its repr -- which is what would
        # reach a log or a traceback -- cannot contain the template.
        for slot in slots:
            assert [type(value) for value in astuple(slot)] == [int, int, int, int]
        assert marker.hex() * 8 not in repr(slots).lower()

    def test_an_empty_store_reads_as_no_slots(self) -> None:
        assert parse_fingerprint_payload(build_fingerprint_payload([])) == []
        assert parse_fingerprint_payload(b"") == []

    def test_a_truncated_store_is_refused_not_guessed_at(self) -> None:
        payload = build_fingerprint_payload([build_fingerprint_entry(uid=1)])
        with pytest.raises(DeviceParseError, match="truncated"):
            parse_fingerprint_payload(payload[:-100])

    def test_an_impossible_entry_size_is_refused(self) -> None:
        from struct import pack

        body = pack("<HHbb", 2, 1, 6, 1)
        with pytest.raises(DeviceParseError, match="smaller than"):
            parse_fingerprint_payload(pack("<I", len(body)) + body)


class TestCapabilityGraduation:
    """What the capability model now claims, and what it still refuses."""

    @pytest.mark.parametrize(
        "capability",
        [
            Capability.WRITE_USERS,
            Capability.DELETE_USERS,
            Capability.WRITE_USER_PASSWORD,
            Capability.READ_FINGERPRINT,
        ],
    )
    def test_hardware_proven_capabilities_report_as_supported(self, capability: Capability) -> None:
        state = NG_MB1_CAPABILITIES.state(capability)
        assert state.support is Support.SUPPORTED
        assert state.proven
        assert "PHASE 15" in state.reason

    @pytest.mark.parametrize(
        "capability",
        [Capability.SET_TIME, Capability.READ_FACE],
    )
    def test_unproven_capabilities_stay_unverified(self, capability: Capability) -> None:
        state = NG_MB1_CAPABILITIES.state(capability)
        assert state.support is Support.UNVERIFIED
        assert not state.proven
        assert not state.usable

    @pytest.mark.parametrize(
        "capability",
        [Capability.WRITE_USER_CARD, Capability.CLEAR_ATTENDANCE],
    )
    def test_withheld_capabilities_stay_unsupported(self, capability: Capability) -> None:
        assert NG_MB1_CAPABILITIES.state(capability).support is Support.UNSUPPORTED

    def test_proving_a_write_did_not_turn_writing_on(self) -> None:
        """The point of Support.OPERATOR_LOCKED."""
        from clockmanager.protocol.interface import DeviceConnectionSettings

        device = NGTecoMB1Device(DeviceConnectionSettings(name="t", host="192.0.2.10"))
        for capability in WRITE_CAPABILITIES:
            state = device.capabilities.state(capability)
            assert state.support is Support.OPERATOR_LOCKED
            assert state.proven, "the device does support it"
            assert not state.usable, "this installation has not enabled it"
            with pytest.raises(DeviceCapabilityError):
                device.capabilities.require(capability)

    def test_locking_cannot_dress_up_an_unproven_capability(self) -> None:
        """locked() must never make an unverified capability look proven."""
        locked = NG_MB1_CAPABILITIES.locked([Capability.SET_TIME], reason="test")
        assert locked.state(Capability.SET_TIME).support is Support.UNVERIFIED

    def test_fingerprint_reading_needs_no_write_unlock(self) -> None:
        """Enumeration is a read and must not require the write gate."""
        from clockmanager.protocol.interface import DeviceConnectionSettings

        device = NGTecoMB1Device(DeviceConnectionSettings(name="t", host="192.0.2.10"))
        device.capabilities.require(Capability.READ_FINGERPRINT)


class TestAttendanceRecordIndex:
    """Corroboration of the PHASE 14 finding with a two-user sample."""

    def test_the_leading_uint16_is_never_reported_as_a_device_uid(self) -> None:
        from datetime import datetime

        from clockmanager.protocol.records import parse_attendance_payload
        from tests.fixtures.mb1 import build_attendance_payload, build_attendance_record

        # As read from the device: six records indexed 1..6, the last belonging
        # to user "2" while carrying index 6.
        records = [
            build_attendance_record(
                uid=index,
                user_id="1" if index < 6 else "2",
                occurred_at=datetime(2026, 9, 4, 15, 20, 25),  # noqa: DTZ001
                status=1,
                punch=0,
                size=40,
            )
            for index in range(1, 7)
        ]
        events = parse_attendance_payload(
            build_attendance_payload(records), record_count=len(records)
        )

        assert [event.user_id for event in events] == ["1"] * 5 + ["2"]
        assert all(event.device_uid is None for event in events)


class TestUserPayloadStillParses:
    """The 120-byte parse is unaffected by everything above."""

    def test_records_with_residue_after_the_terminator_parse_cleanly(self) -> None:
        record = bytearray(
            build_user_record(
                uid=2,
                user_id="2",
                first_name="Erin",
                last_name="Stilo",
                privilege=14,
            )
        )
        record[65:68] = b"nis"  # residue observed on the real device
        users = parse_raw_user_records(build_user_payload([bytes(record)]))

        from clockmanager.protocol.records import parse_user_record

        parsed = parse_user_record(users[0].raw)
        assert parsed.last_name == "Stilo"
        assert parsed.first_name == "Erin"


class TestFingerprintPayloadIsNeverPreviewed:
    """Enumerating fingerprints must not put templates into a trace."""

    def test_a_table_read_payload_is_withheld_whole(self) -> None:
        from clockmanager.protocol.constants import CMD_DB_RRQ
        from clockmanager.protocol.trace import redact_payload_preview

        payload = build_fingerprint_payload(
            [build_fingerprint_entry(uid=1), build_fingerprint_entry(uid=2)]
        )
        preview = redact_payload_preview(payload, command=CMD_DB_RRQ)

        assert "withheld" in preview
        assert str(len(payload)) in preview
        # No hex dump of any kind, so no template byte can appear.
        marker = f"{CREDENTIAL_MARKER_BYTE:02x}"
        assert marker not in preview.split("withheld")[0].lower()
        assert marker * 2 not in preview.lower()

    def test_the_diagnostics_step_reports_metadata_only(self) -> None:
        """The trace's fingerprint step describes slots, never bytes."""
        from clockmanager.services.diagnostics import _describe_fingerprint_slots

        class _Device:
            capabilities = NG_MB1_CAPABILITIES

            @staticmethod
            def read_fingerprint_slots() -> list[FingerprintSlot]:
                return [FingerprintSlot(device_uid=1, finger_index=6, valid=1, template_bytes=838)]

        detail = _describe_fingerprint_slots(_Device())
        assert "UID 1" in detail
        assert "838 template bytes" in detail
        assert f"{CREDENTIAL_MARKER_BYTE:02x}" not in detail.lower()

    def test_the_step_reports_an_empty_store_plainly(self) -> None:
        from clockmanager.services.diagnostics import _describe_fingerprint_slots

        class _Device:
            capabilities = NG_MB1_CAPABILITIES

            @staticmethod
            def read_fingerprint_slots() -> list[FingerprintSlot]:
                return []

        assert _describe_fingerprint_slots(_Device()) == "No fingerprints enrolled."

    def test_a_device_without_the_operation_is_reported_not_raised(self) -> None:
        from clockmanager.services.diagnostics import _describe_fingerprint_slots

        class _Device:
            capabilities = NG_MB1_CAPABILITIES

        assert "Not available" in _describe_fingerprint_slots(_Device())
