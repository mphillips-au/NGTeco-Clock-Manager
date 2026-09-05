"""120-byte MB1 record builder tests.

``TESTING.md`` requires unit tests for the packet builder. These are the tests
that stand between a malformed packet and a real clock, so they check the exact
byte layout, not just that a record was produced.
"""

from __future__ import annotations

import pytest

from clockmanager.domain.users import CredentialAction
from clockmanager.protocol.builders import (
    RawUserRecord,
    build_user_record,
    describe_record_fields,
    encode_fixed_text,
    parse_raw_user_records,
)
from clockmanager.protocol.constants import (
    ADMIN_PRIVILEGE,
    EMPLOYEE_PRIVILEGE,
    MB1_USER_RECORD_SIZE,
    PYZK_USER_PACKET_SIZE,
    USER_CREDENTIAL_SLICE,
    USER_FIRST_NAME_SLICE,
    USER_ID_SLICE,
    USER_LAST_NAME_SLICE,
    USER_PRIVILEGE_OFFSET,
    USER_UID_SLICE,
)
from clockmanager.protocol.errors import DeviceParseError, DeviceValidationError
from clockmanager.protocol.records import parse_user_record
from tests.fixtures.mb1 import CREDENTIAL_MARKER_BYTE, build_user_payload
from tests.fixtures.mb1 import build_user_record as fixture_record


class TestRecordShape:
    def test_builds_exactly_120_bytes(self) -> None:
        record = build_user_record(uid=7, user_id="EMP-007", privilege=EMPLOYEE_PRIVILEGE)
        assert len(record) == MB1_USER_RECORD_SIZE

    def test_is_not_the_generic_pyzk_packet_size(self) -> None:
        """The whole reason this module exists (PROTOCOL.md)."""
        record = build_user_record(uid=1, user_id="1", privilege=EMPLOYEE_PRIVILEGE)
        assert len(record) != PYZK_USER_PACKET_SIZE

    def test_places_every_field_at_the_verified_offset(self) -> None:
        record = build_user_record(
            uid=513,
            user_id="EMP-042",
            first_name="Ada",
            last_name="Lovelace",
            privilege=ADMIN_PRIVILEGE,
        )

        assert record[USER_UID_SLICE] == b"\x01\x02"  # 513 little-endian
        assert record[USER_PRIVILEGE_OFFSET] == ADMIN_PRIVILEGE
        assert record[USER_FIRST_NAME_SLICE].rstrip(b"\x00") == b"Ada"
        assert record[USER_LAST_NAME_SLICE].rstrip(b"\x00") == b"Lovelace"
        assert record[USER_ID_SLICE].rstrip(b"\x00") == b"EMP-042"

    def test_round_trips_through_the_verified_parser(self) -> None:
        """The builder and the device-verified parser must agree."""
        record = build_user_record(
            uid=9,
            user_id="EMP-009",
            first_name="Grace",
            last_name="Hopper",
            privilege=ADMIN_PRIVILEGE,
        )
        user = parse_user_record(record)

        assert user.device_uid == 9
        assert user.user_id == "EMP-009"
        assert user.first_name == "Grace"
        assert user.last_name == "Hopper"
        assert user.privilege == ADMIN_PRIVILEGE
        assert user.is_admin

    def test_round_trips_a_multibyte_name(self) -> None:
        record = build_user_record(
            uid=3, user_id="EMP-003", first_name="José", privilege=EMPLOYEE_PRIVILEGE
        )
        assert parse_user_record(record).first_name == "José"


class TestValidation:
    def test_refuses_an_unverified_privilege(self) -> None:
        """AGENTS.md: only device-verified values may be written."""
        with pytest.raises(DeviceValidationError, match="privilege"):
            build_user_record(uid=1, user_id="1", privilege=7)

    def test_refuses_a_uid_wider_than_the_field(self) -> None:
        with pytest.raises(DeviceValidationError, match="two bytes"):
            build_user_record(uid=70_000, user_id="1", privilege=EMPLOYEE_PRIVILEGE)

    def test_refuses_an_empty_user_id(self) -> None:
        with pytest.raises(DeviceValidationError, match="User ID"):
            build_user_record(uid=1, user_id="   ", privilege=EMPLOYEE_PRIVILEGE)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("first_name", "A" * 25),
            ("last_name", "B" * 24),
            ("user_id", "C" * 10),
        ],
    )
    def test_refuses_a_value_that_does_not_fit_rather_than_truncating(
        self, field: str, value: str
    ) -> None:
        """Silent truncation would store something the operator did not confirm."""
        kwargs: dict[str, object] = {
            "uid": 1,
            "user_id": "1",
            "privilege": EMPLOYEE_PRIVILEGE,
            field: value,
        }
        with pytest.raises(DeviceValidationError, match="fit"):
            build_user_record(**kwargs)  # type: ignore[arg-type]

    def test_refuses_an_embedded_nul(self) -> None:
        with pytest.raises(DeviceValidationError, match="NUL"):
            build_user_record(uid=1, user_id="A\x00B", privilege=EMPLOYEE_PRIVILEGE)

    def test_multibyte_names_are_measured_in_bytes_not_characters(self) -> None:
        """24 accented characters encode to 48 bytes and must be refused."""
        with pytest.raises(DeviceValidationError, match="does not fit"):
            build_user_record(uid=1, user_id="1", first_name="é" * 24, privilege=EMPLOYEE_PRIVILEGE)

    def test_encode_fixed_text_pads_to_the_exact_width(self) -> None:
        assert encode_fixed_text("Ada", 6, what="First name") == b"Ada\x00\x00\x00"


class TestCredentialRegion:
    def _existing_with_credential(self) -> RawUserRecord:
        raw = fixture_record(
            uid=4,
            user_id="EMP-004",
            first_name="Katherine",
            last_name="Johnson",
            with_credential=True,
        )
        return RawUserRecord(uid=4, user_id="EMP-004", raw=raw)

    def test_preserve_copies_the_existing_region_byte_for_byte(self) -> None:
        """A name change must never destroy a PIN."""
        existing = self._existing_with_credential()
        record = build_user_record(
            uid=4,
            user_id="EMP-004",
            first_name="Kate",
            privilege=EMPLOYEE_PRIVILEGE,
            credential_action=CredentialAction.PRESERVE,
            existing=existing,
        )

        assert record[USER_CREDENTIAL_SLICE] == existing.credential_region
        assert set(record[USER_CREDENTIAL_SLICE]) == {CREDENTIAL_MARKER_BYTE}

    def test_a_new_user_gets_an_empty_region_rather_than_a_guess(self) -> None:
        record = build_user_record(
            uid=1,
            user_id="NEW-1",
            privilege=EMPLOYEE_PRIVILEGE,
            credential_action=CredentialAction.PRESERVE,
            existing=None,
        )
        assert not any(record[USER_CREDENTIAL_SLICE])

    def test_clear_zeroes_the_whole_region(self) -> None:
        record = build_user_record(
            uid=4,
            user_id="EMP-004",
            privilege=EMPLOYEE_PRIVILEGE,
            credential_action=CredentialAction.CLEAR,
            existing=self._existing_with_credential(),
        )
        assert not any(record[USER_CREDENTIAL_SLICE])

    def test_set_writes_only_the_candidate_field_and_preserves_the_rest(self) -> None:
        """A wrong guess at the layout must damage as little as possible."""
        existing = self._existing_with_credential()
        record = build_user_record(
            uid=4,
            user_id="EMP-004",
            privilege=EMPLOYEE_PRIVILEGE,
            credential_action=CredentialAction.SET,
            existing=existing,
            password="1234",
        )

        region = record[USER_CREDENTIAL_SLICE]
        assert region[:8] == b"1234\x00\x00\x00\x00"
        # Everything outside the candidate field is untouched.
        assert region[8:] == existing.credential_region[8:]

    def test_set_requires_a_value(self) -> None:
        with pytest.raises(DeviceValidationError, match="requires a value"):
            build_user_record(
                uid=1,
                user_id="1",
                privilege=EMPLOYEE_PRIVILEGE,
                credential_action=CredentialAction.SET,
                password="",
            )

    def test_a_credential_never_appears_in_a_repr(self) -> None:
        """SECURITY.md: a traceback must not be able to disclose it."""
        rendered = repr(self._existing_with_credential())
        assert str(CREDENTIAL_MARKER_BYTE) not in rendered
        assert "raw" not in rendered
        assert "EMP-004" in rendered


class TestRawRecordParsing:
    def test_splits_a_payload_into_whole_records(self) -> None:
        payload = build_user_payload(
            [
                fixture_record(uid=1, user_id="1001", first_name="Ada", with_credential=True),
                fixture_record(uid=2, user_id="1002", first_name="Grace"),
            ]
        )
        records = parse_raw_user_records(payload)

        assert [record.uid for record in records] == [1, 2]
        assert [record.user_id for record in records] == ["1001", "1002"]
        assert records[0].has_credential_data
        assert not records[1].has_credential_data

    def test_refuses_a_payload_that_is_not_a_multiple_of_120(self) -> None:
        with pytest.raises(DeviceParseError, match="not a multiple"):
            parse_raw_user_records(build_user_payload([b"\x00" * 72]))

    def test_empty_payload_yields_nothing(self) -> None:
        assert parse_raw_user_records(b"") == []

    def test_a_record_must_be_exactly_120_bytes(self) -> None:
        with pytest.raises(DeviceParseError, match="120 bytes"):
            RawUserRecord(uid=1, user_id="1", raw=b"\x00" * 119)


class TestRecordDescription:
    def test_describes_fields_without_the_credential_contents(self) -> None:
        record = build_user_record(
            uid=4,
            user_id="EMP-004",
            first_name="Katherine",
            last_name="Johnson",
            privilege=ADMIN_PRIVILEGE,
            credential_action=CredentialAction.SET,
            password="4321",
        )
        described = describe_record_fields(record)

        assert described["uid"] == "4"
        assert described["user_id"] == "EMP-004"
        assert described["privilege"] == str(ADMIN_PRIVILEGE)
        assert described["credential_present"] == "True"
        assert "4321" not in str(described)
