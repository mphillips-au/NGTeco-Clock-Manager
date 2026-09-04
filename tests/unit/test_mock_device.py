"""Mock device tests.

TESTING.md requires a simulated device covering connect, device info, users,
attendance, live events and failures.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from clockmanager.domain.models import AttendanceEvent, PunchDirection
from clockmanager.protocol.capabilities import Capability
from clockmanager.protocol.errors import (
    DeviceCapabilityError,
    DeviceConnectionError,
    DeviceNotConnectedError,
)
from clockmanager.protocol.interface import AttendanceDevice
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript, sample_settings
from clockmanager.protocol.records import parse_user_payload
from tests.fixtures.mb1 import sample_users

MOMENT = datetime(2026, 3, 1, 9, 0, 0)  # noqa: DTZ001


def _script(**overrides: object) -> MockDeviceScript:
    base = MockDeviceScript(users=parse_user_payload(sample_users()))
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_mock_satisfies_the_device_interface() -> None:
    assert isinstance(MockAttendanceDevice(), AttendanceDevice)


def test_mock_uses_a_documentation_address_that_cannot_reach_hardware() -> None:
    """RFC 5737 reserves 192.0.2.0/24 for documentation."""
    assert sample_settings().host.startswith("192.0.2.")


def test_connect_and_device_info() -> None:
    device = MockAttendanceDevice(script=_script())
    info = device.connect()

    assert device.is_connected
    assert info.identity.model == "NG-MB1"
    assert info.identity.platform == "ZMM510_TFT"
    assert info.user_count == 3


def test_disconnect() -> None:
    device = MockAttendanceDevice(script=_script())
    device.connect()
    device.disconnect()
    assert not device.is_connected
    assert device.disconnect_calls == 1


def test_context_manager() -> None:
    device = MockAttendanceDevice(script=_script())
    with device:
        assert device.is_connected
    assert not device.is_connected


def test_reads_require_a_connection() -> None:
    device = MockAttendanceDevice(script=_script())
    for read in (device.get_users, device.get_attendance, device.get_device_time):
        with pytest.raises(DeviceNotConnectedError):
            read()


def test_get_users() -> None:
    device = MockAttendanceDevice(script=_script())
    device.connect()
    users = device.get_users()
    assert [u.user_id for u in users] == ["1001", "1002", "EMP-003"]
    assert users[0].is_admin


def test_get_attendance() -> None:
    events = [
        AttendanceEvent(user_id="1001", occurred_at=MOMENT, punch=0),
        AttendanceEvent(user_id="1001", occurred_at=MOMENT, punch=1, status=3),
    ]
    device = MockAttendanceDevice(script=_script(attendance=events))
    device.connect()

    read = device.get_attendance()
    assert [e.direction for e in read] == [PunchDirection.IN, PunchDirection.OUT]
    assert read[1].status == 3


def test_get_device_time() -> None:
    device = MockAttendanceDevice(script=_script(device_time=MOMENT))
    device.connect()
    assert device.get_device_time() == MOMENT


def test_live_capture_yields_events_and_idle_ticks() -> None:
    events: list[AttendanceEvent | None] = [
        AttendanceEvent(user_id="1001", occurred_at=MOMENT, punch=0),
        None,
        AttendanceEvent(user_id="1002", occurred_at=MOMENT, punch=1),
    ]
    device = MockAttendanceDevice(script=_script(live_events=events))
    device.connect()

    received = list(device.live_capture())
    assert len(received) == 3
    assert received[1] is None
    assert received[0] is not None and received[0].direction is PunchDirection.IN


def test_live_capture_can_be_stopped() -> None:
    events: list[AttendanceEvent | None] = [
        AttendanceEvent(user_id=str(index), occurred_at=MOMENT, punch=0) for index in range(10)
    ]
    device = MockAttendanceDevice(script=_script(live_events=events))
    device.connect()

    received = []
    for event in device.live_capture():
        received.append(event)
        if len(received) == 3:
            device.stop_live_capture()
    assert len(received) == 3


def test_scripted_connect_failure_then_success() -> None:
    device = MockAttendanceDevice(script=_script(connect_failures=2))
    for _ in range(2):
        with pytest.raises(DeviceConnectionError):
            device.connect()
    assert device.connect().identity.model == "NG-MB1"
    assert device.connect_calls == 3


def test_scripted_read_failure() -> None:
    device = MockAttendanceDevice(script=_script(read_failures=1))
    device.connect()
    with pytest.raises(DeviceConnectionError):
        device.get_users()
    assert len(device.get_users()) == 3


def test_mock_refuses_unverified_capabilities_like_the_real_adapter() -> None:
    device = MockAttendanceDevice(script=_script())
    device.connect()
    with pytest.raises(DeviceCapabilityError):
        device.capabilities.require(Capability.WRITE_USERS)


def test_mock_exposes_no_write_operations() -> None:
    public = {name for name in dir(MockAttendanceDevice) if not name.startswith("_")}
    assert not any("write" in name or "delete" in name for name in public)
