"""PHASE 14 production-QA hardening.

Every test here pins a defect found by exercising the application against the
real NG-MB1 (serial NBF6260700048, platform ZMM510_TFT, firmware
Ver 8.0.4.5-7108-02) rather than against fixtures alone. The device evidence
for each one is recorded in ``PROTOCOL.md``.
"""

from __future__ import annotations

import io
from datetime import datetime
from struct import pack

import pytest
from sqlalchemy import text

from clockmanager.__main__ import make_console_output_safe
from clockmanager.persistence.database import create_database, initialise_database
from clockmanager.persistence.migrations import MIGRATIONS
from clockmanager.persistence.models import SCHEMA_VERSION
from clockmanager.protocol.constants import (
    CMD_ATTLOG_RRQ,
    CMD_USERTEMP_RRQ,
    MAX_DEVICE_YEAR,
    MIN_DEVICE_YEAR,
)
from clockmanager.protocol.errors import DeviceParseError
from clockmanager.protocol.records import (
    decode_zk_time,
    decode_zk_timehex,
    parse_attendance_payload,
)
from clockmanager.protocol.trace import format_hex_preview, redact_payload_preview
from tests.fixtures.mb1 import (
    build_attendance_payload,
    build_attendance_record,
    build_user_payload,
    build_user_record,
)


class TestFortyByteAttendanceLeadingField:
    """The 40-byte record's leading uint16 is a record index, not a user UID.

    Observed on the real MB1: three punches by the single enrolled user (device
    UID 1) carried 1, 2 and 3 in that field, while the 24-byte user-ID text
    read "1" in all three.
    """

    MOMENT = datetime(2026, 9, 4, 15, 20, 25)  # noqa: DTZ001 (device-local wall time)

    def _payload(self, count: int) -> bytes:
        records = [
            build_attendance_record(
                size=40,
                uid=index + 1,  # the record index, as the device emits it
                user_id="1",
                occurred_at=self.MOMENT,
                punch=index % 2,
            )
            for index in range(count)
        ]
        return build_attendance_payload(records)

    def test_record_index_is_never_reported_as_a_device_uid(self) -> None:
        events = parse_attendance_payload(self._payload(3), record_count=3)

        assert [event.user_id for event in events] == ["1", "1", "1"]
        # The old parser produced device_uid 1, 2, 3 here -- three different
        # "users" for one person.
        assert [event.device_uid for event in events] == [None, None, None]

    def test_a_record_with_no_user_id_is_refused_not_attributed_to_the_index(self) -> None:
        """Falling back to the index would invent a user that is not enrolled."""
        record = build_attendance_record(
            size=40, uid=7, user_id="", occurred_at=self.MOMENT, punch=0
        )

        with pytest.raises(DeviceParseError, match="no user ID"):
            parse_attendance_payload(build_attendance_payload([record]), record_count=1)

    def test_the_eight_byte_form_still_reports_a_real_device_uid(self) -> None:
        """Only the 40-byte form carries an index; the 8-byte form is a UID."""
        users = build_user_payload([build_user_record(uid=3, user_id="1003")])
        record = build_attendance_record(size=8, uid=3, occurred_at=self.MOMENT, punch=1)

        from clockmanager.protocol.records import parse_user_payload

        event = parse_attendance_payload(
            build_attendance_payload([record]),
            record_count=1,
            users=parse_user_payload(users),
        )[0]

        assert event.device_uid == 3
        assert event.user_id == "1003"


class TestTimestampRangeGuard:
    """The packed encodings have no invalid representation, so bound the year."""

    def test_an_all_ones_packed_timestamp_is_refused(self) -> None:
        """0xffffffff used to decode to a plausible-looking punch in 2133."""
        with pytest.raises(DeviceParseError, match="2133"):
            decode_zk_time(b"\xff\xff\xff\xff")

    @pytest.mark.parametrize(
        "moment",
        [datetime(2000, 1, 1), datetime(2026, 9, 4, 15, 20, 25)],  # noqa: DTZ001
    )
    def test_plausible_timestamps_still_decode(self, moment: datetime) -> None:
        from tests.fixtures.mb1 import encode_zk_time

        assert decode_zk_time(encode_zk_time(moment)) == moment

    def test_a_live_event_year_beyond_the_range_is_refused(self) -> None:
        with pytest.raises(DeviceParseError, match="outside the plausible range"):
            decode_zk_timehex(bytes([200, 9, 4, 12, 0, 0]))  # year 2200

    def test_the_range_brackets_the_encodings_epoch(self) -> None:
        assert MIN_DEVICE_YEAR == 2000
        assert MAX_DEVICE_YEAR == 2099

    def test_a_corrupt_timestamp_cannot_reach_stored_attendance(self) -> None:
        """The refusal has to happen during the parse, not at display time."""
        record = pack(
            "<H24sB4sB8s", 1, b"1".ljust(24, b"\x00"), 0, b"\xff\xff\xff\xff", 0, bytes(8)
        )

        with pytest.raises(DeviceParseError):
            parse_attendance_payload(build_attendance_payload([record]), record_count=1)


class TestPayloadPreviewClassification:
    """A trace preview must be decided by the command, not by the byte count."""

    MOMENT = datetime(2026, 9, 4, 15, 20, 25)  # noqa: DTZ001 (device-local wall time)

    def _attendance_payload(self) -> bytes:
        # Three 40-byte records = a 120-byte body, exactly one user-record
        # length. This is the ordinary case on the project MB1.
        records = [
            build_attendance_record(
                size=40, uid=index + 1, user_id="1", occurred_at=self.MOMENT, punch=index % 2
            )
            for index in range(3)
        ]
        return build_attendance_payload(records)

    def test_attendance_bytes_are_shown_exactly_as_they_arrived(self) -> None:
        payload = self._attendance_payload()
        assert len(payload) == 124  # 4-byte prefix + 120-byte body

        preview = redact_payload_preview(payload, command=CMD_ATTLOG_RRQ)

        # Previously this blanked bytes 3:35 of the "record", destroying the
        # first punch's user ID, status, timestamp and direction.
        assert preview == format_hex_preview(payload)

    def test_user_records_are_still_redacted(self) -> None:
        secret = bytes(range(1, 33))
        record = bytearray(build_user_record(uid=1, user_id="1", first_name="Dean"))
        record[3:35] = secret
        payload = build_user_payload([bytes(record)])

        preview = redact_payload_preview(payload, command=CMD_USERTEMP_RRQ)

        assert secret.hex() not in preview.replace(" ", "").replace("\n", "")
        assert "44 65 61 6e" in preview  # "Dean" survives; only the secret goes

    def test_user_data_of_an_unknown_shape_is_withheld_rather_than_shown(self) -> None:
        """The dangerous case: user data the redactor cannot map is never dumped."""
        truncated = pack("<I", 60) + bytes(range(1, 61))

        preview = redact_payload_preview(truncated, command=CMD_USERTEMP_RRQ)

        assert "withheld" in preview
        assert "01 02 03" not in preview

    def test_an_unknown_command_still_errs_towards_redaction(self) -> None:
        secret = bytes(range(1, 33))
        record = bytearray(build_user_record(uid=1, user_id="1"))
        record[3:35] = secret
        payload = build_user_payload([bytes(record)])

        preview = redact_payload_preview(payload, command=None)

        assert secret.hex() not in preview.replace(" ", "").replace("\n", "")


class TestConsoleOutputEncoding:
    """A stock cmd.exe code page must not be able to kill the process."""

    def test_streams_are_reconfigured_to_replace_unencodable_characters(self) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp437", errors="strict")

        make_console_output_safe(stream)

        stream.write("NGTeco Clock Manager — attendance")  # em dash
        stream.flush()
        assert stream.errors == "replace"

    def test_a_stream_without_reconfigure_is_left_alone(self) -> None:
        class Plain:
            pass

        make_console_output_safe(Plain())  # must not raise

    def test_the_cli_help_survives_a_legacy_code_page(self) -> None:
        """``clockmanager --help`` used to raise UnicodeEncodeError on cp437."""
        from clockmanager.__main__ import _build_parser

        buffer = io.TextIOWrapper(io.BytesIO(), encoding="cp437", errors="strict")
        make_console_output_safe(buffer)
        _build_parser().print_help(buffer)
        buffer.flush()


class TestDeviceUidMigration:
    """Schema 8 clears the indices earlier syncs stored as user UIDs."""

    def test_migration_8_is_registered_for_the_current_schema(self) -> None:
        assert SCHEMA_VERSION == 8
        assert MIGRATIONS[-1].version == 8

    def test_stored_record_indices_are_cleared_and_punches_are_kept(self, tmp_path) -> None:
        from clockmanager.config import AppConfig, AppPaths

        config = AppConfig(paths=AppPaths(tmp_path))
        config.paths.ensure()
        database = create_database(config)
        initialise_database(database)

        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO devices (name, port, communication_password, timeout_seconds, "
                    "auto_reconnect, sync_interval_seconds, enabled, created_at, updated_at) "
                    "VALUES ('Clock', 4370, 0, 10, 1, 300, 1, :now, :now)"
                ),
                {"now": "2026-09-04 10:00:00"},
            )
            for index in (1, 2, 3):
                connection.execute(
                    text(
                        "INSERT INTO attendance_events "
                        "(device_id, device_uid, user_id, occurred_at, punch, status, "
                        " received_at, source, event_key) "
                        "VALUES (1, :uid, '1', :when, 0, 1, :when, 'historical', :key)"
                    ),
                    {"uid": index, "when": f"2026-09-04 1{index}:00:00", "key": f"key{index}"},
                )
            connection.execute(
                text("UPDATE schema_info SET value = '7' WHERE key = 'schema_version'")
            )
        database.dispose()

        migrated = create_database(config)
        assert initialise_database(migrated) == SCHEMA_VERSION

        with migrated.engine.begin() as connection:
            rows = connection.execute(
                text("SELECT device_uid, user_id, punch FROM attendance_events ORDER BY id")
            ).all()
        migrated.dispose()

        # The bogus UIDs are gone; every punch and its identity survive.
        assert rows == [(None, "1", 0), (None, "1", 0), (None, "1", 0)]
