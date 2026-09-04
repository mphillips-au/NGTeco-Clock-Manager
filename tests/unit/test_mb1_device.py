"""NGTecoMB1Device adapter tests.

A fake transport stands in for ``pyzk.ZK``, so the adapter's behaviour is
exercised without a socket and without a real clock.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from zk.exception import ZKErrorConnection, ZKErrorResponse, ZKNetworkError

from clockmanager.domain.models import PunchDirection
from clockmanager.protocol.capabilities import Capability, Support
from clockmanager.protocol.constants import CMD_ATTLOG_RRQ, CMD_USERTEMP_RRQ
from clockmanager.protocol.errors import (
    DeviceCapabilityError,
    DeviceConnectionError,
    DeviceNotConnectedError,
    DeviceProtocolError,
    DeviceTimeoutError,
)
from clockmanager.protocol.interface import (
    AttendanceDevice,
    DeviceConnectionSettings,
    WritableUserDevice,
)
from clockmanager.protocol.mb1 import NGTecoMB1Device
from clockmanager.protocol.retry import RetryPolicy
from tests.fixtures.mb1 import (
    build_attendance_payload,
    build_attendance_record,
    sample_users,
)

MOMENT = datetime(2026, 2, 3, 7, 55, 0)  # noqa: DTZ001


def settings(**overrides: Any) -> DeviceConnectionSettings:
    base: dict[str, Any] = {
        "name": "Bench clock",
        "host": "192.0.2.10",  # RFC 5737 documentation range
        "timeout_seconds": 1.0,
    }
    base.update(overrides)
    return DeviceConnectionSettings(**base)


class FakeTransport:
    """Stands in for ``pyzk.ZK``, mimicking the surface the adapter uses."""

    def __init__(
        self,
        *,
        user_payload: bytes | None = None,
        attendance_payload: bytes | None = None,
        records: int = 0,
        connect_error: Exception | None = None,
        read_error: Exception | None = None,
        read_error_times: int = 0,
    ) -> None:
        self.user_payload = user_payload if user_payload is not None else sample_users()
        self.attendance_payload = (
            attendance_payload if attendance_payload is not None else build_attendance_payload([])
        )
        self.users = 3
        self.records = records
        self.fingers = 0
        self.faces = 0
        self.tcp = True
        self.is_enabled = True
        self.connect_error = connect_error
        self.read_error = read_error
        self.read_error_times = read_error_times
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.commands: list[int] = []

    def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def _maybe_fail(self) -> None:
        if self.read_error is not None and self.read_error_times > 0:
            self.read_error_times -= 1
            raise self.read_error

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
        self._maybe_fail()
        self.commands.append(command)
        payload = self.user_payload if command == CMD_USERTEMP_RRQ else self.attendance_payload
        return payload, len(payload)


def build_device(transport: FakeTransport, **kwargs: Any) -> NGTecoMB1Device:
    kwargs.setdefault("retry_policy", RetryPolicy(attempts=1, initial_backoff_seconds=0))
    return NGTecoMB1Device(settings(), transport_factory=lambda _s: transport, **kwargs)


class TestInterfaceConformance:
    def test_adapter_satisfies_the_device_interface(self) -> None:
        device = build_device(FakeTransport())
        assert isinstance(device, AttendanceDevice)

    def test_adapter_satisfies_the_writable_interface(self) -> None:
        device = build_device(FakeTransport(), allow_writes=True)
        assert isinstance(device, WritableUserDevice)

    def test_adapter_exposes_no_destructive_operations_beyond_user_delete(self) -> None:
        """PHASE 03 adds user writes only.

        Clearing attendance, resetting the device and writing biometric
        templates stay absent: none of them has device evidence, and a method
        that exists is a method something can call.
        """
        forbidden = (
            "set_user",
            "clear_attendance",
            "restart",
            "poweroff",
            "set_time",
            "write_fingerprint",
            "write_face",
            "save_user_template",
        )
        public = {name for name in dir(NGTecoMB1Device) if not name.startswith("_")}
        assert not public & set(forbidden)

    def test_generic_pyzk_user_writer_is_never_called(self) -> None:
        """AGENTS.md: pyzk.set_user() must not be used for MB1 writes.

        Checked against the parsed syntax tree rather than the text, so the
        prose explaining why it is not used cannot fail the test, and a real
        call cannot hide inside a string.
        """
        source = Path("src") / (NGTecoMB1Device.__module__.replace(".", "/") + ".py")
        text = source.read_text(encoding="utf-8")
        tree = ast.parse(text)

        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "set_user" not in called
        assert "save_user_template" not in called
        assert "clear_attendance" not in called
        assert "CMD_USER_WRQ" in text


class TestConnection:
    def test_connect_returns_device_info(self) -> None:
        device = build_device(FakeTransport())
        info = device.connect()

        assert device.is_connected
        assert info.identity.model == "NG-MB1"
        assert info.identity.platform == "ZMM510_TFT"
        assert info.identity.firmware_version == "Ver 8.0.4.5-7108-02"
        assert info.device_time == MOMENT

    def test_connect_failure_is_translated(self) -> None:
        transport = FakeTransport(connect_error=ZKNetworkError("unreachable"))
        device = build_device(transport)
        with pytest.raises(DeviceConnectionError, match=re.escape("192.0.2.10:4370")):
            device.connect()
        assert not device.is_connected

    def test_os_error_on_connect_is_translated(self) -> None:
        device = build_device(FakeTransport(connect_error=OSError("no route to host")))
        with pytest.raises(DeviceConnectionError):
            device.connect()

    def test_disconnect_is_idempotent(self) -> None:
        transport = FakeTransport()
        device = build_device(transport)
        device.connect()
        device.disconnect()
        device.disconnect()
        assert not device.is_connected
        assert transport.disconnect_calls == 1

    def test_disconnect_swallows_transport_errors(self) -> None:
        transport = FakeTransport()
        transport.disconnect = _raise(ZKErrorConnection("already gone"))  # type: ignore[method-assign]
        device = build_device(transport)
        device.connect()
        device.disconnect()
        assert not device.is_connected

    def test_context_manager_connects_and_disconnects(self) -> None:
        transport = FakeTransport()
        with build_device(transport) as device:
            assert device.is_connected
        assert transport.disconnect_calls == 1

    def test_reads_require_a_connection(self) -> None:
        device = build_device(FakeTransport())
        with pytest.raises(DeviceNotConnectedError):
            device.get_users()


class TestReads:
    def test_get_users_uses_the_120_byte_parser(self) -> None:
        transport = FakeTransport()
        device = build_device(transport)
        device.connect()

        users = device.get_users()
        assert [u.user_id for u in users] == ["1001", "1002", "EMP-003"]
        assert users[0].is_admin
        assert CMD_USERTEMP_RRQ in transport.commands

    def test_get_attendance_maps_uids_using_our_users(self) -> None:
        record = build_attendance_record(size=8, uid=3, occurred_at=MOMENT, punch=1)
        transport = FakeTransport(attendance_payload=build_attendance_payload([record]), records=1)
        device = build_device(transport)
        device.connect()

        events = device.get_attendance()
        assert len(events) == 1
        assert events[0].user_id == "EMP-003"
        assert events[0].direction is PunchDirection.OUT
        assert CMD_ATTLOG_RRQ in transport.commands

    def test_get_device_time(self) -> None:
        device = build_device(FakeTransport())
        device.connect()
        assert device.get_device_time() == MOMENT

    def test_device_info_counts_are_reported(self) -> None:
        transport = FakeTransport(records=42)
        device = build_device(transport)
        info = device.connect()
        assert info.user_count == 3
        assert info.attendance_count == 42

    def test_device_info_survives_a_clock_that_will_not_report(self) -> None:
        transport = FakeTransport()
        transport.get_time = _raise(ZKErrorResponse("no clock"))  # type: ignore[method-assign]
        device = build_device(transport)
        info = device.connect()
        assert info.device_time is None
        assert info.identity.platform == "ZMM510_TFT"


class TestErrorTranslation:
    def test_timeout_is_translated(self) -> None:
        transport = FakeTransport(read_error=TimeoutError(), read_error_times=99)
        device = build_device(transport)
        device.connect()
        with pytest.raises(DeviceTimeoutError):
            device.get_users()

    def test_protocol_rejection_is_translated_and_not_retried(self) -> None:
        transport = FakeTransport(read_error=ZKErrorResponse("refused"), read_error_times=99)
        device = build_device(
            transport, retry_policy=RetryPolicy(attempts=3, initial_backoff_seconds=0)
        )
        device.connect()
        with pytest.raises(DeviceProtocolError):
            device.get_users()

    def test_network_error_is_translated(self) -> None:
        transport = FakeTransport(read_error=ZKNetworkError("dropped"), read_error_times=99)
        device = build_device(transport)
        device.connect()
        with pytest.raises(DeviceConnectionError):
            device.get_users()


class TestRetryAndReconnect:
    def test_transient_failure_is_retried_after_reconnecting(self) -> None:
        transport = FakeTransport(read_error=ZKNetworkError("dropped"), read_error_times=1)
        device = build_device(
            transport,
            retry_policy=RetryPolicy(attempts=3, initial_backoff_seconds=0),
            auto_reconnect=True,
        )
        device.connect()
        assert transport.connect_calls == 1

        users = device.get_users()
        assert len(users) == 3
        assert transport.connect_calls == 2  # reconnected once before the retry
        assert transport.disconnect_calls == 1

    def test_retries_are_bounded(self) -> None:
        transport = FakeTransport(read_error=ZKNetworkError("dropped"), read_error_times=99)
        device = build_device(
            transport, retry_policy=RetryPolicy(attempts=2, initial_backoff_seconds=0)
        )
        device.connect()
        with pytest.raises(DeviceConnectionError, match="after 2 attempt"):
            device.get_users()

    def test_auto_reconnect_can_be_disabled(self) -> None:
        transport = FakeTransport(read_error=ZKNetworkError("dropped"), read_error_times=1)
        device = build_device(
            transport,
            retry_policy=RetryPolicy(attempts=2, initial_backoff_seconds=0),
            auto_reconnect=False,
        )
        device.connect()
        assert device.get_users()
        assert transport.connect_calls == 1  # retried in place, never reconnected


class TestCapabilityEnforcement:
    def test_unverified_and_unsupported_capabilities_are_refused(self) -> None:
        device = build_device(FakeTransport())
        for capability in (
            Capability.WRITE_USERS,
            Capability.DELETE_USERS,
            Capability.SET_TIME,
            Capability.CLEAR_ATTENDANCE,
            Capability.READ_FACE,
            Capability.READ_FINGERPRINT,
        ):
            with pytest.raises(DeviceCapabilityError):
                device.capabilities.require(capability)

    def test_verified_read_capabilities_are_allowed(self) -> None:
        device = build_device(FakeTransport())
        for capability in (
            Capability.CONNECT,
            Capability.DEVICE_INFO,
            Capability.READ_TIME,
            Capability.READ_USERS,
            Capability.READ_ATTENDANCE,
            Capability.LIVE_CAPTURE,
        ):
            device.capabilities.require(capability)

    def test_writing_is_locked_until_an_operator_unlocks_it(self) -> None:
        """No MB1 has accepted a record from this path, so it starts locked."""
        state = build_device(FakeTransport()).capabilities.state(Capability.WRITE_USERS)
        assert state.support is Support.UNVERIFIED
        assert not state.usable
        assert "120-byte" in state.reason

    def test_unlocking_reports_operator_enabled_never_verified(self) -> None:
        """An unlocked capability must not masquerade as proven on hardware."""
        state = build_device(FakeTransport(), allow_writes=True).capabilities.state(
            Capability.WRITE_USERS
        )
        assert state.support is Support.OPERATOR_ENABLED
        assert state.usable
        assert not state.proven

    def test_card_writing_stays_unsupported_and_cannot_be_unlocked(self) -> None:
        """PROTOCOL.md: no card field has been identified in the MB1 record."""
        capabilities = build_device(FakeTransport(), allow_writes=True).capabilities
        assert not capabilities.supports(Capability.WRITE_USER_CARD)
        with pytest.raises(DeviceCapabilityError):
            capabilities.unlocked([Capability.WRITE_USER_CARD], reason="test")

    def test_clearing_attendance_stays_unsupported(self) -> None:
        capabilities = build_device(FakeTransport(), allow_writes=True).capabilities
        assert not capabilities.supports(Capability.CLEAR_ATTENDANCE)


class TestConnectionSettings:
    def test_communication_password_is_not_in_repr(self) -> None:
        """SECURITY.md: the comm password must not reach a log or traceback."""
        rendered = repr(settings(communication_password=123456))
        assert "123456" not in rendered
        assert "192.0.2.10" in rendered

    def test_endpoint_is_safe_to_log(self) -> None:
        assert settings(communication_password=999).endpoint == "192.0.2.10:4370"

    def test_password_presence_is_reportable(self) -> None:
        assert settings(communication_password=1234).has_communication_password
        assert not settings().has_communication_password

    @pytest.mark.parametrize(
        "overrides",
        [
            {"name": "  "},
            {"host": ""},
            {"port": 0},
            {"port": 70000},
            {"timeout_seconds": 0},
            {"timeout_seconds": -1},
        ],
    )
    def test_invalid_settings_rejected(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            settings(**overrides)

    def test_no_default_host_exists(self) -> None:
        """AGENTS.md: no hardcoded production IP anywhere in source."""
        with pytest.raises(TypeError):
            DeviceConnectionSettings(name="No host")  # type: ignore[call-arg]


def _raise(error: Exception):  # type: ignore[no-untyped-def]
    def _raiser(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return _raiser
