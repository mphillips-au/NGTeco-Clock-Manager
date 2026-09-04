"""MB1 record parser tests against sanitized fixtures.

These are the tests that protect the one thing generic ZKTeco tooling gets
wrong on this device: the 120-byte user record.
"""

from __future__ import annotations

from datetime import datetime
from struct import pack

import pytest

from clockmanager.domain.models import Privilege, PunchDirection
from clockmanager.protocol.constants import MB1_USER_RECORD_SIZE
from clockmanager.protocol.errors import DeviceParseError
from clockmanager.protocol.records import (
    decode_zk_time,
    decode_zk_timehex,
    parse_attendance_payload,
    parse_live_event,
    parse_user_payload,
    parse_user_record,
    split_size_prefixed_payload,
)
from tests.fixtures.mb1 import (
    ADMIN_PRIVILEGE,
    CREDENTIAL_MARKER_BYTE,
    EMPLOYEE_PRIVILEGE,
    build_attendance_payload,
    build_attendance_record,
    build_live_event,
    build_user_payload,
    build_user_record,
    encode_zk_time,
    sample_users,
)


class TestUserRecord:
    def test_record_size_is_120_not_the_generic_zkteco_shapes(self) -> None:
        record = build_user_record(uid=1, user_id="1001")
        assert len(record) == MB1_USER_RECORD_SIZE
        assert len(record) not in (28, 72)

    def test_parses_verified_field_offsets(self) -> None:
        record = build_user_record(
            uid=4660,
            user_id="EMP-0042",
            first_name="Ada",
            last_name="Lovelace",
            privilege=ADMIN_PRIVILEGE,
        )
        user = parse_user_record(record)

        assert user.device_uid == 4660
        assert user.privilege == ADMIN_PRIVILEGE
        assert user.first_name == "Ada"
        assert user.last_name == "Lovelace"
        assert user.user_id == "EMP-0042"

    def test_uid_is_little_endian_uint16(self) -> None:
        record = build_user_record(uid=0x1234, user_id="1001")
        assert record[0:2] == b"\x34\x12"
        assert parse_user_record(record).device_uid == 0x1234

    def test_maximum_uid(self) -> None:
        assert parse_user_record(build_user_record(uid=65535, user_id="1")).device_uid == 65535

    @pytest.mark.parametrize(
        ("privilege", "is_admin", "label"),
        [(EMPLOYEE_PRIVILEGE, False, "Employee"), (ADMIN_PRIVILEGE, True, "Admin")],
    )
    def test_verified_privileges(self, privilege: int, is_admin: bool, label: str) -> None:
        user = parse_user_record(build_user_record(uid=1, user_id="1", privilege=privilege))
        assert user.is_admin is is_admin
        assert user.privilege_label == label

    def test_unknown_privilege_is_preserved_not_guessed(self) -> None:
        user = parse_user_record(build_user_record(uid=1, user_id="1", privilege=7))
        assert user.privilege == 7
        assert Privilege.from_raw(user.privilege) is None
        assert user.privilege_label == "Unknown (7)"
        assert not user.is_admin

    def test_names_are_nul_terminated_and_trimmed(self) -> None:
        user = parse_user_record(
            build_user_record(uid=1, user_id="1001", first_name="Ada", last_name="Lovelace")
        )
        assert user.first_name == "Ada"
        assert "\x00" not in user.first_name
        assert "\x00" not in user.last_name

    def test_non_ascii_names(self) -> None:
        user = parse_user_record(
            build_user_record(uid=1, user_id="1001", first_name="José", last_name="Müller")
        )
        assert user.first_name == "José"
        assert user.last_name == "Müller"

    def test_maximum_length_names_fill_their_regions(self) -> None:
        first, last, user_id = "F" * 24, "L" * 37, "U" * 24
        user = parse_user_record(
            build_user_record(uid=1, user_id=user_id, first_name=first, last_name=last)
        )
        assert user.first_name == first
        assert user.last_name == last
        assert user.user_id == user_id

    def test_missing_last_name(self) -> None:
        user = parse_user_record(
            build_user_record(uid=1, user_id="1001", first_name="Prince", last_name="")
        )
        assert user.last_name == ""
        assert user.display_name == "Prince"

    def test_empty_user_id_falls_back_to_uid_rather_than_inventing_one(self) -> None:
        user = parse_user_record(build_user_record(uid=77, user_id=""))
        assert user.user_id == "77"

    @pytest.mark.parametrize("size", [0, 27, 28, 72, 119, 121])
    def test_wrong_record_size_is_rejected(self, size: int) -> None:
        with pytest.raises(DeviceParseError, match="120 bytes"):
            parse_user_record(bytes(size))


class TestCredentialRegion:
    """SECURITY.md: the credential/PIN region is never decoded or returned."""

    def test_credential_bytes_never_appear_in_the_parsed_user(self) -> None:
        record = build_user_record(uid=1, user_id="1001", first_name="Ada", with_credential=True)
        user = parse_user_record(record)

        rendered = repr(user)
        assert chr(CREDENTIAL_MARKER_BYTE) not in rendered
        assert f"{CREDENTIAL_MARKER_BYTE:02x}" not in rendered.lower()
        for value in vars(user).values() if hasattr(user, "__dict__") else []:
            assert CREDENTIAL_MARKER_BYTE not in (value if isinstance(value, bytes) else b"")

    def test_presence_is_reported_without_contents(self) -> None:
        with_credential = parse_user_record(
            build_user_record(uid=1, user_id="1001", with_credential=True)
        )
        without = parse_user_record(build_user_record(uid=2, user_id="1002"))
        assert with_credential.has_credential_data is True
        assert without.has_credential_data is False

    def test_no_field_holds_the_region_bytes(self) -> None:
        user = parse_user_record(build_user_record(uid=1, user_id="1001", with_credential=True))
        values = [getattr(user, name) for name in user.__slots__]
        assert not any(isinstance(value, bytes | bytearray) for value in values)


class TestUserPayload:
    def test_parses_multiple_users(self) -> None:
        users = parse_user_payload(sample_users())
        assert [u.user_id for u in users] == ["1001", "1002", "EMP-003"]
        assert [u.device_uid for u in users] == [1, 2, 3]
        assert users[0].is_admin
        assert not users[1].is_admin

    def test_empty_payload(self) -> None:
        assert parse_user_payload(b"") == []
        assert parse_user_payload(pack("<I", 0)) == []

    def test_payload_shorter_than_size_prefix_is_rejected(self) -> None:
        with pytest.raises(DeviceParseError, match="size prefix"):
            parse_user_payload(b"\x01\x02")

    def test_trailing_padding_beyond_declared_size_is_ignored(self) -> None:
        record = build_user_record(uid=1, user_id="1001")
        payload = build_user_payload([record]) + b"\x00" * 16
        assert len(parse_user_payload(payload)) == 1

    def test_non_multiple_of_120_is_refused_rather_than_guessed(self) -> None:
        body = build_user_record(uid=1, user_id="1001")[:100]
        payload = pack("<I", len(body)) + body
        with pytest.raises(DeviceParseError, match="Refusing to guess"):
            parse_user_payload(payload)

    def test_generic_72_byte_layout_is_refused(self) -> None:
        """A device sending the generic ZKTeco shape must not be silently parsed."""
        body = bytes(72 * 2)
        with pytest.raises(DeviceParseError):
            parse_user_payload(pack("<I", len(body)) + body)

    def test_split_size_prefixed_payload(self) -> None:
        declared, body = split_size_prefixed_payload(pack("<I", 5) + b"abcde", what="Test")
        assert declared == 5
        assert body == b"abcde"


class TestTimeDecoding:
    def test_round_trip(self) -> None:
        moment = datetime(2026, 3, 15, 14, 32, 9)  # noqa: DTZ001
        assert decode_zk_time(encode_zk_time(moment)) == moment

    @pytest.mark.parametrize(
        "moment",
        [
            datetime(2000, 1, 1, 0, 0, 0),  # noqa: DTZ001
            datetime(2026, 12, 31, 23, 59, 59),  # noqa: DTZ001
            datetime(2026, 2, 3, 7, 55, 0),  # noqa: DTZ001
        ],
    )
    def test_round_trip_boundaries(self, moment: datetime) -> None:
        assert decode_zk_time(encode_zk_time(moment)) == moment

    def test_result_is_naive_device_local_time(self) -> None:
        assert decode_zk_time(encode_zk_time(datetime(2026, 3, 1, 9, 0))).tzinfo is None  # noqa: DTZ001

    @pytest.mark.parametrize("size", [0, 3, 5])
    def test_wrong_length_rejected(self, size: int) -> None:
        with pytest.raises(DeviceParseError, match="4 bytes"):
            decode_zk_time(bytes(size))

    def test_timehex_decoding(self) -> None:
        assert decode_zk_timehex(pack("6B", 26, 3, 15, 14, 32, 9)) == datetime(  # noqa: DTZ001
            2026, 3, 15, 14, 32, 9
        )

    def test_timehex_wrong_length_rejected(self) -> None:
        with pytest.raises(DeviceParseError, match="6 bytes"):
            decode_zk_timehex(bytes(4))

    def test_invalid_timehex_rejected(self) -> None:
        with pytest.raises(DeviceParseError, match="invalid live timestamp"):
            decode_zk_timehex(pack("6B", 26, 13, 40, 99, 99, 99))


class TestAttendancePayload:
    MOMENT = datetime(2026, 2, 3, 7, 55, 0)  # noqa: DTZ001

    @pytest.mark.parametrize("size", [8, 16, 40])
    def test_all_record_sizes(self, size: int) -> None:
        record = build_attendance_record(
            size=size, uid=1, user_id="1001", occurred_at=self.MOMENT, punch=0, status=0
        )
        events = parse_attendance_payload(
            build_attendance_payload([record]),
            record_count=1,
            users=parse_user_payload(sample_users()),
        )
        assert len(events) == 1
        assert events[0].occurred_at == self.MOMENT
        assert events[0].user_id == "1001"

    def test_punch_gives_direction_and_status_is_preserved_raw(self) -> None:
        records = [
            build_attendance_record(
                size=40, uid=1, user_id="1001", occurred_at=self.MOMENT, punch=0, status=5
            ),
            build_attendance_record(
                size=40, uid=1, user_id="1001", occurred_at=self.MOMENT, punch=1, status=9
            ),
        ]
        events = parse_attendance_payload(build_attendance_payload(records), record_count=2)

        assert events[0].direction is PunchDirection.IN
        assert events[0].status == 5
        assert events[1].direction is PunchDirection.OUT
        assert events[1].status == 9

    def test_unknown_punch_has_no_direction(self) -> None:
        record = build_attendance_record(
            size=40, uid=1, user_id="1001", occurred_at=self.MOMENT, punch=4
        )
        event = parse_attendance_payload(build_attendance_payload([record]), record_count=1)[0]
        assert event.direction is None
        assert event.direction_label == "Unknown (4)"

    def test_eight_byte_records_map_uid_to_user_id_via_our_parser(self) -> None:
        """The 8-byte form carries only a UID; the mapping must use MB1 users."""
        users = parse_user_payload(sample_users())
        record = build_attendance_record(size=8, uid=3, occurred_at=self.MOMENT, punch=1)
        event = parse_attendance_payload(
            build_attendance_payload([record]), record_count=1, users=users
        )[0]
        assert event.user_id == "EMP-003"
        assert event.device_uid == 3

    def test_eight_byte_record_with_unknown_uid_falls_back_to_the_uid(self) -> None:
        record = build_attendance_record(size=8, uid=99, occurred_at=self.MOMENT)
        event = parse_attendance_payload(
            build_attendance_payload([record]), record_count=1, users=[]
        )[0]
        assert event.user_id == "99"

    def test_many_records(self) -> None:
        records = [
            build_attendance_record(
                size=40, uid=1, user_id="1001", occurred_at=self.MOMENT, punch=index % 2
            )
            for index in range(50)
        ]
        events = parse_attendance_payload(build_attendance_payload(records), record_count=50)
        assert len(events) == 50
        assert sum(1 for e in events if e.direction is PunchDirection.IN) == 25

    def test_empty_payload(self) -> None:
        assert parse_attendance_payload(b"") == []
        assert parse_attendance_payload(pack("<I", 0)) == []

    def test_unrecognised_record_size_is_refused(self) -> None:
        body = bytes(13)
        with pytest.raises(DeviceParseError, match="does not divide"):
            parse_attendance_payload(pack("<I", len(body)) + body, record_count=1)

    def test_ambiguous_length_without_a_usable_count_is_refused(self) -> None:
        """Every multiple of 40 is also a multiple of 8 and 16.

        Guessing here would silently turn two real records into ten fabricated
        ones, so an ambiguous payload must fail loudly instead.
        """
        records = [
            build_attendance_record(size=40, uid=1, user_id="1001", occurred_at=self.MOMENT)
            for _ in range(2)
        ]
        with pytest.raises(DeviceParseError, match="ambiguous"):
            parse_attendance_payload(build_attendance_payload(records), record_count=0)

    def test_unambiguous_length_is_parsed_without_a_count(self) -> None:
        """24 bytes divides only by 8, so there is nothing to guess at."""
        records = [
            build_attendance_record(size=8, uid=1, occurred_at=self.MOMENT) for _ in range(3)
        ]
        events = parse_attendance_payload(build_attendance_payload(records), record_count=0)
        assert len(events) == 3

    def test_device_record_count_is_authoritative(self) -> None:
        """With the count supplied, a 40-byte payload parses correctly."""
        records = [
            build_attendance_record(size=40, uid=1, user_id="1001", occurred_at=self.MOMENT)
            for _ in range(2)
        ]
        events = parse_attendance_payload(build_attendance_payload(records), record_count=2)
        assert len(events) == 2
        assert all(e.user_id == "1001" for e in events)


class TestLiveEvents:
    MOMENT = datetime(2026, 3, 1, 9, 15, 30)  # noqa: DTZ001

    @pytest.mark.parametrize("size", [12, 32, 36, 52])
    def test_all_live_layouts(self, size: int) -> None:
        event = parse_live_event(
            build_live_event(size=size, user_id="1001", occurred_at=self.MOMENT, punch=1, status=2)
        )
        assert event.user_id == "1001"
        assert event.occurred_at == self.MOMENT
        assert event.direction is PunchDirection.OUT
        assert event.status == 2

    def test_non_numeric_user_id_in_wide_layouts(self) -> None:
        event = parse_live_event(
            build_live_event(size=52, user_id="EMP-003", occurred_at=self.MOMENT)
        )
        assert event.user_id == "EMP-003"

    def test_unknown_layout_is_refused(self) -> None:
        with pytest.raises(DeviceParseError, match="does not match any known layout"):
            parse_live_event(bytes(20))

    def test_blank_user_id_is_refused(self) -> None:
        with pytest.raises(DeviceParseError, match="no user identifier"):
            parse_live_event(build_live_event(size=32, user_id="", occurred_at=self.MOMENT))
