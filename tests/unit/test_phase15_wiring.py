"""The PHASE 15 capabilities, wired through the application.

`phases/PHASE-15.md` proved four things on the real NG-MB1 that the
application then did not use: device options can be read, capacities are
reported, fingerprints can be enumerated, and the device keeps its own
operation log. These tests pin the wiring for each of them, end to end --
parser, adapter, capability gate, service -- and pin the limits that came with
them:

* no template byte, PIN or communication password may reach a caller,
* no option outside the allow-list may be requested,
* nothing here may acquire a way to *write* a device option,
* a section the device will not answer must degrade to a note, not an
  exception, and "unknown" must never be presented as "none".
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any

import pytest

from clockmanager.domain.models import (
    DeviceOption,
    DeviceStorage,
    FingerprintSlot,
    StorageCounter,
)
from clockmanager.protocol.capabilities import (
    NG_MB1_CAPABILITIES,
    Capability,
    Support,
)
from clockmanager.protocol.constants import (
    CMD_DB_RRQ,
    CMD_OPTIONS_RRQ,
    FCT_OPLOG,
    OPERATION_LOG_RECORD_SIZE,
)
from clockmanager.protocol.errors import DeviceCapabilityError, DeviceParseError
from clockmanager.protocol.interface import DeviceConnectionSettings, InspectableDevice
from clockmanager.protocol.mb1 import NGTecoMB1Device
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript
from clockmanager.protocol.options import (
    NG_MB1_OPTIONS,
    is_sensitive_option_name,
    option_specs,
)
from clockmanager.protocol.records import (
    parse_device_option_response,
    parse_operation_log_payload,
)
from clockmanager.protocol.retry import RetryPolicy
from clockmanager.protocol.trace import RecordingTransport, TraceRecorder, redact_payload_preview
from clockmanager.services.audit import AuditService
from clockmanager.services.devices import DeviceProfile, DeviceService
from clockmanager.services.users import UserService
from tests.fixtures.mb1 import (
    build_fingerprint_entry,
    build_fingerprint_payload,
    build_operation_log_payload,
    build_operation_log_record,
    sample_users,
)

MOMENT = datetime(2026, 3, 1, 9, 0, 0)  # noqa: DTZ001


# -- a transport that answers option reads ------------------------------------


class OptionTransport:
    """A ``pyzk.ZK`` stand-in that answers the reads this phase added.

    Option reads go through pyzk's name-mangled ``__send_command``, so the
    mangled name is what the adapter looks for and what this fake provides.
    """

    def __init__(
        self,
        *,
        answers: dict[str, str] | None = None,
        oplog_payload: bytes | None = None,
        fingerprint_payload: bytes | None = None,
        sizes: dict[str, int] | None = None,
    ) -> None:
        self.answers = answers if answers is not None else {"~PIN2Width": "9", "VOLUME": "70"}
        self.oplog_payload = oplog_payload if oplog_payload is not None else b""
        self.fingerprint_payload = fingerprint_payload if fingerprint_payload is not None else b""
        self.tcp = True
        self.requested: list[str] = []
        self.buffered: list[tuple[int, int]] = []
        self._ZK__data = b""
        for name, value in (sizes or {}).items():
            setattr(self, name, value)

    # pyzk surface -----------------------------------------------------------

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def get_device_name(self) -> str:
        return "NG-MB1"

    def get_serialnumber(self) -> str:
        return "TEST-SERIAL-0001"

    def get_platform(self) -> str:
        return "ZMM510_TFT"

    def get_firmware_version(self) -> str:
        return "Ver 8.0.4.5-7108-02"

    def get_time(self) -> datetime:
        return MOMENT

    def read_sizes(self) -> bool:
        return True

    def read_with_buffer(self, command: int, fct: int = 0, ext: int = 0) -> tuple[bytes, int]:
        self.buffered.append((command, fct))
        if command == CMD_DB_RRQ and fct == FCT_OPLOG:
            return self.oplog_payload, len(self.oplog_payload)
        if command == CMD_DB_RRQ:
            return self.fingerprint_payload, len(self.fingerprint_payload)
        payload = sample_users()
        return payload, len(payload)

    def _ZK__send_command(  # noqa: N802 - mimics pyzk's name-mangled method
        self, command: int, data: bytes = b"", response_size: int = 8
    ) -> dict[str, Any]:
        assert command == CMD_OPTIONS_RRQ
        assert data.endswith(b"\x00"), "option names are NUL-terminated"
        name = data[:-1].decode("ascii")
        self.requested.append(name)
        if name not in self.answers:
            # What the real device does for an option its firmware lacks.
            self._ZK__data = b""
            return {"status": False, "code": 4999}
        self._ZK__data = f"{name}={self.answers[name]}".encode() + b"\x00"
        return {"status": True, "code": 2000}


def build_device(transport: OptionTransport, **kwargs: Any) -> NGTecoMB1Device:
    kwargs.setdefault("retry_policy", RetryPolicy(attempts=1, initial_backoff_seconds=0))
    return NGTecoMB1Device(
        DeviceConnectionSettings(name="Bench clock", host="192.0.2.10", timeout_seconds=1.0),
        transport_factory=lambda _s: transport,
        **kwargs,
    )


# -- the option catalogue -----------------------------------------------------


class TestOptionCatalogue:
    def test_every_catalogued_name_is_safe_to_request(self) -> None:
        """SECURITY.md: no catalogued option may be able to carry a credential."""
        assert [spec.name for spec in NG_MB1_OPTIONS if is_sensitive_option_name(spec.name)] == []

    @pytest.mark.parametrize(
        "name", ["ComKey", "comkey", " Password ", "DevicePwd", "api_token", "SecretPin"]
    )
    def test_credential_bearing_names_are_refused(self, name: str) -> None:
        assert is_sensitive_option_name(name)

    def test_a_name_outside_the_catalogue_is_not_read(self) -> None:
        """The allow-list is what stops this becoming arbitrary value fishing."""
        assert option_specs(["ComKey", "~PIN2Width"]) == option_specs(["~PIN2Width"])

    def test_the_ip_address_option_carries_its_warning(self) -> None:
        """PHASE 15: the stored address is not the address the device answers on."""
        spec = next(spec for spec in NG_MB1_OPTIONS if spec.name == "IPAddress")
        assert "Never" in spec.note
        assert "reconnect" in spec.note


class TestOptionResponseParsing:
    def test_reads_the_value_after_the_equals_sign(self) -> None:
        assert parse_device_option_response(b"~PIN2Width=9\x00", name="~PIN2Width") == "9"

    def test_tolerates_trailing_padding(self) -> None:
        raw = b"MAC=00:00:5e:00:53:01\x00\x00\x00\x00"
        assert parse_device_option_response(raw, name="MAC") == "00:00:5e:00:53:01"

    def test_refuses_a_reply_for_a_different_option(self) -> None:
        """A desynchronised session must not report one setting under another's name."""
        with pytest.raises(DeviceParseError, match="Refusing to report"):
            parse_device_option_response(b"VOLUME=70\x00", name="~PIN2Width")

    def test_refuses_a_reply_that_is_not_name_equals_value(self) -> None:
        with pytest.raises(DeviceParseError, match="Name=Value"):
            parse_device_option_response(b"garbage\x00", name="VOLUME")


class TestReadingOptionsFromTheDevice:
    def test_reads_the_whole_catalogue_and_reports_refusals_as_answers(self) -> None:
        transport = OptionTransport(answers={"~PIN2Width": "9"})
        device = build_device(transport)
        device.connect()

        options = device.read_device_options()

        assert [spec.name for spec in NG_MB1_OPTIONS] == transport.requested
        answered = {option.name: option.value for option in options if option.answered}
        assert answered == {"~PIN2Width": "9"}
        refused = next(option for option in options if option.name == "VOLUME")
        assert refused.value is None
        assert refused.display_value == "Not available on this model"

    def test_reads_only_the_named_subset(self) -> None:
        transport = OptionTransport()
        device = build_device(transport)
        device.connect()

        options = device.read_device_options(["VOLUME"])

        assert transport.requested == ["VOLUME"]
        assert [option.value for option in options] == ["70"]

    def test_a_name_outside_the_catalogue_is_never_sent(self) -> None:
        transport = OptionTransport()
        device = build_device(transport)
        device.connect()

        assert device.read_device_options(["ComKey"]) == []
        assert transport.requested == []


class TestTracedOptionReads:
    """Option reads must go through the recorder, not around it.

    A device settings read is a plain command rather than a buffered read, and
    pyzk only exposes its command sender name-mangled. If the recording
    transport could not reach it, a traced session would either bypass the
    trace or fail outright — so this pins both the recording and the values.
    """

    def test_options_are_recorded_and_still_answered(self) -> None:
        recorder = TraceRecorder()
        transport = OptionTransport(answers={"VOLUME": "70"})
        device = build_device(RecordingTransport(transport, recorder))
        device.connect()

        options = device.read_device_options(["VOLUME"])

        assert [option.value for option in options] == ["70"]
        commands = [event.label for event in recorder.events]
        assert f"CMD {CMD_OPTIONS_RRQ}" in commands
        assert f"ACK {CMD_OPTIONS_RRQ}" in commands

    def test_no_option_name_reaches_the_trace_as_raw_bytes(self) -> None:
        """The trace summarises the payload; it never needs to dump it."""
        recorder = TraceRecorder()
        device = build_device(RecordingTransport(OptionTransport(), recorder))
        device.connect()
        device.read_device_options(["~PIN2Width"])

        details = " ".join(event.detail for event in recorder.events)
        assert b"~PIN2Width".hex() not in details.replace(" ", "")


class TestStorage:
    def test_capacities_are_read_from_the_size_response(self) -> None:
        transport = OptionTransport(
            sizes={
                "users": 2,
                "users_cap": 200,
                "users_av": 198,
                "fingers": 2,
                "fingers_cap": 400,
                "fingers_av": 398,
                "records": 6,
                "rec_cap": 30000,
                "rec_av": 29994,
                "faces": 2,
                "faces_cap": 200,
                "dummy": 33,
            }
        )
        device = build_device(transport)
        device.connect()

        storage = device.read_storage()

        assert storage.users.describe() == "2 of 200 used — 198 free"
        assert storage.attendance.describe() == "6 of 30,000 used — 29,994 free"
        assert storage.fingerprints.percent_used == pytest.approx(0.5)
        assert storage.operation_log_records == 33

    def test_capacities_reach_the_connection_snapshot(self) -> None:
        """The dashboard and diagnostics both render ``DeviceInfo.as_rows()``."""
        transport = OptionTransport(sizes={"users": 2, "users_cap": 200, "records": 6})
        info = build_device(transport).connect()

        assert info.storage is not None
        assert ("Users on device", "2 of 200 used — 198 free") in info.as_rows()

    def test_a_missing_count_is_not_reported_as_zero(self) -> None:
        counter = StorageCounter("Users")
        assert counter.describe() == "Not reported"
        assert counter.free is None
        assert counter.percent_used is None

    def test_the_card_counter_is_not_surfaced(self) -> None:
        """PHASE 15: nothing establishes what pyzk's ``cards`` field counts."""
        transport = OptionTransport(sizes={"cards": 2})
        storage = build_device(transport).connect().storage

        assert storage is not None
        assert "card" not in " ".join(label for label, _ in storage.as_rows()).lower()


class TestOperationLog:
    def test_parses_sixteen_byte_records(self) -> None:
        payload = build_operation_log_payload(
            [
                build_operation_log_record(operation=5, operator_uid=1, occurred_at=MOMENT),
                build_operation_log_record(
                    operation=6, operator_uid=2, parameters=(3, 0, 0), occurred_at=MOMENT
                ),
            ]
        )

        entries = parse_operation_log_payload(payload)

        assert [entry.operation for entry in entries] == [5, 6]
        assert [entry.operator_uid for entry in entries] == [1, 2]
        assert entries[0].occurred_at == MOMENT
        assert entries[1].parameters == (3, 0, 0)

    def test_operation_codes_are_never_given_invented_names(self) -> None:
        """Only the timestamp inside a record is verified on this device."""
        entry = parse_operation_log_payload(
            build_operation_log_payload([build_operation_log_record(operation=42)])
        )[0]
        assert entry.operation_label == "Operation 42"

    def test_one_unreadable_timestamp_does_not_lose_the_other_records(self) -> None:
        payload = build_operation_log_payload(
            [
                build_operation_log_record(raw_time=b"\xff\xff\xff\xff"),
                build_operation_log_record(occurred_at=MOMENT),
            ]
        )

        entries = parse_operation_log_payload(payload)

        assert entries[0].occurred_at is None
        assert entries[0].occurred_label == "Unreadable timestamp"
        assert entries[1].occurred_at == MOMENT

    def test_refuses_a_payload_that_is_not_whole_records(self) -> None:
        payload = build_operation_log_payload([build_operation_log_record()[:-3]])
        with pytest.raises(DeviceParseError, match="whole number"):
            parse_operation_log_payload(payload)

    def test_reads_from_the_device_through_the_table_selector(self) -> None:
        payload = build_operation_log_payload([build_operation_log_record(occurred_at=MOMENT)])
        transport = OptionTransport(oplog_payload=payload)
        device = build_device(transport)
        device.connect()

        entries = device.read_operation_log()

        assert (CMD_DB_RRQ, FCT_OPLOG) in transport.buffered
        assert len(entries) == 1

    def test_the_payload_is_never_previewed_in_a_trace(self) -> None:
        """A table read may carry templates, so it is withheld whole."""
        payload = build_operation_log_payload([build_operation_log_record()])
        preview = redact_payload_preview(payload, command=CMD_DB_RRQ)

        assert "withheld" in preview
        assert payload[4:8].hex() not in preview.replace(" ", "")


# -- capability model ---------------------------------------------------------


class TestCapabilities:
    @pytest.mark.parametrize(
        "capability",
        [
            Capability.READ_DEVICE_OPTIONS,
            Capability.READ_STORAGE,
            Capability.READ_OPERATION_LOG,
            Capability.READ_FINGERPRINT,
        ],
    )
    def test_the_reads_proven_in_phase_15_are_supported(self, capability: Capability) -> None:
        state = NG_MB1_CAPABILITIES.state(capability)
        assert state.support is Support.SUPPORTED
        assert "PHASE 15" in state.reason

    def test_writing_a_device_option_stays_unsupported(self) -> None:
        state = NG_MB1_CAPABILITIES.state(Capability.WRITE_DEVICE_OPTIONS)
        assert state.support is Support.UNSUPPORTED
        assert not state.usable

    def test_an_unsupported_capability_cannot_be_unlocked_by_an_operator(self) -> None:
        with pytest.raises(DeviceCapabilityError, match="cannot be unlocked"):
            NG_MB1_CAPABILITIES.unlocked(
                [Capability.WRITE_DEVICE_OPTIONS], reason="Trying it anyway."
            )

    def test_no_option_write_exists_anywhere_in_the_adapter(self) -> None:
        device = build_device(OptionTransport())
        for forbidden in ("write_device_options", "set_option", "write_option"):
            assert not hasattr(device, forbidden)


class TestInterfaceConformance:
    def test_the_real_adapter_can_describe_itself(self) -> None:
        assert isinstance(build_device(OptionTransport()), InspectableDevice)

    def test_the_mock_can_describe_itself(self) -> None:
        assert isinstance(MockAttendanceDevice(), InspectableDevice)


# -- service layer ------------------------------------------------------------


@pytest.fixture
def profile(context: Any) -> DeviceProfile:
    return context.devices.save_profile(
        DeviceProfile(name="Bench clock", host="192.0.2.10", port=4370)
    )


def mock_service(context: Any, device: MockAttendanceDevice) -> DeviceService:
    """A device service that always hands out ``device``."""
    return DeviceService(context.database, device_factory=lambda _profile, **_kw: device)


def seeded_mock(**overrides: Any) -> MockAttendanceDevice:
    from clockmanager.domain.models import DeviceUser

    script = MockDeviceScript(
        users=[
            DeviceUser(device_uid=1, user_id="1", first_name="Ada", last_name="Lovelace"),
            DeviceUser(
                device_uid=2,
                user_id="2",
                first_name="Grace",
                last_name="Hopper",
                has_credential_data=True,
            ),
        ],
        fingerprints=[FingerprintSlot(device_uid=1, finger_index=6, valid=1, template_bytes=838)],
        operation_log=[
            entry
            for entry in parse_operation_log_payload(
                build_operation_log_payload([build_operation_log_record(occurred_at=MOMENT)])
            )
        ],
    )
    for name, value in overrides.items():
        setattr(script, name, value)
    return MockAttendanceDevice(script=script)


class TestDeviceInspection:
    def test_reads_every_section_in_one_connection(
        self, context: Any, profile: DeviceProfile
    ) -> None:
        device = seeded_mock()
        service = mock_service(context, device)

        inspection = service.inspect(profile)

        assert inspection.ok
        assert inspection.notes == ()
        assert inspection.storage is not None
        assert inspection.storage.users.capacity == 200
        assert any(option.name == "~PIN2Width" for option in inspection.options)
        assert len(inspection.fingerprints) == 1
        assert len(inspection.operation_log) == 1
        assert device.connect_calls == 1
        assert device.disconnect_calls == 1

    def test_a_failed_connection_is_a_result_not_an_exception(
        self, context: Any, profile: DeviceProfile
    ) -> None:
        device = MockAttendanceDevice(script=MockDeviceScript(connect_failures=1))
        inspection = mock_service(context, device).inspect(profile)

        assert not inspection.ok
        assert "refused connection" in inspection.error
        assert "Could not read" in inspection.summary

    def test_one_unavailable_section_does_not_cost_the_others(
        self, context: Any, profile: DeviceProfile, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device = seeded_mock()

        def _refuse() -> list[Any]:
            raise DeviceParseError("the operation log would not decode")

        monkeypatch.setattr(device, "read_operation_log", _refuse)
        inspection = mock_service(context, device).inspect(profile)

        assert inspection.ok
        assert inspection.operation_log == ()
        assert any("operation log" in note.lower() for note in inspection.notes)
        assert inspection.options  # the other sections still arrived

    def test_carries_no_credential_or_template(self, context: Any, profile: DeviceProfile) -> None:
        """SECURITY.md: an inspection is safe to display, export and screenshot.

        The check is for raw bytes anywhere in the structure rather than for a
        keyword: a template, a PIN region and a communication password are all
        bytes, and none of them has any business on a type the GUI renders.
        """
        inspection = mock_service(context, seeded_mock()).inspect(profile)

        def _values(value: Any) -> Iterator[Any]:
            yield value
            if is_dataclass(value) and not isinstance(value, type):
                for item in fields(value):
                    yield from _values(getattr(value, item.name))
            elif isinstance(value, tuple | list):
                for item in value:
                    yield from _values(item)

        assert not [value for value in _values(inspection) if isinstance(value, bytes | bytearray)]
        assert "password" not in repr(inspection).lower()
        # The one thing said about a template is how long it was.
        assert [slot.template_bytes for slot in inspection.fingerprints] == [838]


class TestUserEnrolment:
    def test_fingerprints_are_matched_to_users_by_device_uid(
        self, context: Any, profile: DeviceProfile
    ) -> None:
        service = UserService(mock_service(context, seeded_mock()), AuditService(context.database))

        entries = service.list_enrolment(profile)

        by_uid = {entry.user.device_uid: entry for entry in entries}
        assert by_uid[1].fingers == (6,)
        assert by_uid[1].fingerprint_label == "1"
        assert by_uid[2].fingers == ()
        assert by_uid[2].fingerprint_label == "None"

    def test_a_user_with_a_pin_or_a_finger_can_identify_themselves(
        self, context: Any, profile: DeviceProfile
    ) -> None:
        service = UserService(mock_service(context, seeded_mock()), AuditService(context.database))

        entries = {entry.user.device_uid: entry for entry in service.list_enrolment(profile)}

        assert entries[1].can_identify  # fingerprint only
        assert entries[2].can_identify  # PIN only

    def test_unknown_enrolment_is_never_shown_as_none(
        self, context: Any, profile: DeviceProfile, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A device that will not answer has said nothing, not "nobody is enrolled"."""
        device = seeded_mock()

        def _refuse() -> list[FingerprintSlot]:
            raise DeviceParseError("the fingerprint store would not enumerate")

        monkeypatch.setattr(device, "read_fingerprint_slots", _refuse)
        service = UserService(mock_service(context, device), AuditService(context.database))

        entries = service.list_enrolment(profile)

        assert len(entries) == 2, "the user list survives a failed secondary read"
        assert all(not entry.fingerprints_known for entry in entries)
        assert all(entry.fingerprint_label == "Unknown" for entry in entries)


class TestMockFidelity:
    def test_the_mock_holds_no_real_device_configuration(self) -> None:
        """A fixture must never be mistakable for a real device's settings."""
        device = MockAttendanceDevice()
        device.connect()

        values = {option.name: option.value for option in device.read_device_options()}

        assert values["~SerialNumber"].startswith("MOCK-")
        assert values["IPAddress"].startswith("192.0.2."), "RFC 5737 documentation range"

    def test_option_reads_are_gated_on_the_capability(self) -> None:
        device = MockAttendanceDevice()
        device.connect()
        device._capabilities = NG_MB1_CAPABILITIES.locked(
            [Capability.READ_DEVICE_OPTIONS], reason="Withheld for this test."
        )

        with pytest.raises(DeviceCapabilityError):
            device.read_device_options()

    def test_reported_usage_matches_what_the_mock_actually_holds(self) -> None:
        device = seeded_mock()
        device.connect()

        storage = device.read_storage()

        assert storage.users.used == 2
        assert storage.fingerprints.used == 1
        assert storage.operation_log_records == 1


def test_a_fingerprint_read_reports_length_but_never_content() -> None:
    """The parser is the boundary: template bytes stop there (SECURITY.md)."""
    from clockmanager.protocol.records import parse_fingerprint_payload

    payload = build_fingerprint_payload(
        [build_fingerprint_entry(uid=1, finger_index=6, template_bytes=838)]
    )

    slots = parse_fingerprint_payload(payload)

    assert slots == [FingerprintSlot(device_uid=1, finger_index=6, valid=1, template_bytes=838)]
    # Every field of a slot is a number. There is nowhere for a template to be.
    assert all(isinstance(getattr(slots[0], item.name), int) for item in fields(FingerprintSlot))


def test_operation_log_record_size_is_the_measured_one() -> None:
    """528 bytes / 33 records on the real device (PHASE 15)."""
    assert OPERATION_LOG_RECORD_SIZE == 16
    assert 33 * OPERATION_LOG_RECORD_SIZE == 528


def test_device_option_display_never_invents_a_value() -> None:
    option = DeviceOption(name="WorkCode", label="Work codes enabled", group="Terminal behaviour")
    assert not option.answered
    assert option.display_value == "Not available on this model"


def test_storage_rows_are_safe_to_display() -> None:
    storage = DeviceStorage(users=StorageCounter("Users", 2, 200, 198))
    assert ("Users", "2 of 200 used — 198 free") in storage.as_rows()
