"""Live-capture tests for the MB1 adapter.

The adapter drives the live loop itself rather than using ``pyzk.live_capture()``
because that helper resolves user IDs through its own 28/72-byte user parser and
falls back to ``int(user_id)``, which raises on a non-numeric MB1 user ID. These
tests pin that behaviour: a non-numeric ID must survive live capture.
"""

from __future__ import annotations

import socket
from datetime import datetime
from struct import pack
from typing import Any

import pytest

from clockmanager.domain.models import PunchDirection
from clockmanager.protocol.constants import CMD_REG_EVENT, EF_ATTLOG
from clockmanager.protocol.errors import DeviceConnectionError, DeviceProtocolError
from clockmanager.protocol.mb1 import NGTecoMB1Device
from clockmanager.protocol.retry import RetryPolicy
from tests.fixtures.mb1 import build_live_event
from tests.unit.test_mb1_device import FakeTransport, settings

MOMENT = datetime(2026, 3, 1, 9, 15, 30)  # noqa: DTZ001


def tcp_datagram(body: bytes) -> bytes:
    """Wrap an event body in the TCP framing the adapter expects."""
    return pack("<HHI", 0x5050, 0x827D, len(body) + 8) + pack("HHHH", CMD_REG_EVENT, 0, 0, 0) + body


class FakeSocket:
    """Replays a scripted sequence of datagrams, then blocks with timeouts."""

    def __init__(self, datagrams: list[bytes | type[socket.timeout]]) -> None:
        self._datagrams = list(datagrams)
        self.timeout_value: float | None = None
        self.settimeout_calls: list[float | None] = []

    def recv(self, _size: int) -> bytes:
        if not self._datagrams:
            raise TimeoutError
        item = self._datagrams.pop(0)
        if item is socket.timeout:
            raise TimeoutError
        assert isinstance(item, bytes)
        return item

    def gettimeout(self) -> float | None:
        return self.timeout_value

    def settimeout(self, value: float | None) -> None:
        self.timeout_value = value
        self.settimeout_calls.append(value)


class LiveTransport(FakeTransport):
    """A fake transport that also provides the live-capture surface."""

    def __init__(self, datagrams: list[bytes | type[socket.timeout]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._ZK__sock = FakeSocket(datagrams)
        self._ZK__ack_ok = self._acknowledge
        self.registered_events: list[int] = []
        self.acknowledgements = 0
        self.cancel_calls = 0
        self.verify_calls = 0

    @property
    def fake_socket(self) -> FakeSocket:
        return self._ZK__sock

    def _acknowledge(self) -> None:
        self.acknowledgements += 1

    def cancel_capture(self) -> None:
        self.cancel_calls += 1

    def verify_user(self) -> None:
        self.verify_calls += 1

    def enable_device(self) -> None:
        self.is_enabled = True

    def disable_device(self) -> None:
        self.is_enabled = False

    def reg_event(self, flags: int) -> None:
        self.registered_events.append(flags)


def build_live_device(transport: LiveTransport) -> NGTecoMB1Device:
    return NGTecoMB1Device(
        settings(),
        transport_factory=lambda _s: transport,
        retry_policy=RetryPolicy(attempts=1, initial_backoff_seconds=0),
    )


def test_yields_events_from_a_datagram() -> None:
    body = build_live_event(size=52, user_id="1001", occurred_at=MOMENT, punch=0)
    transport = LiveTransport([tcp_datagram(body)])
    device = build_live_device(transport)
    device.connect()

    stream = device.live_capture(timeout_seconds=0.01)
    event = next(stream)

    assert event is not None
    assert event.user_id == "1001"
    assert event.occurred_at == MOMENT
    assert event.direction is PunchDirection.IN
    stream.close()


def test_non_numeric_user_id_survives_live_capture() -> None:
    """The exact case that makes pyzk's own live_capture raise ValueError."""
    body = build_live_event(size=52, user_id="EMP-003", occurred_at=MOMENT, punch=1)
    transport = LiveTransport([tcp_datagram(body)])
    device = build_live_device(transport)
    device.connect()

    stream = device.live_capture(timeout_seconds=0.01)
    event = next(stream)

    assert event is not None
    assert event.user_id == "EMP-003"
    assert event.direction is PunchDirection.OUT
    stream.close()


def test_status_is_preserved_and_direction_comes_from_punch() -> None:
    body = build_live_event(size=52, user_id="1001", occurred_at=MOMENT, punch=1, status=6)
    transport = LiveTransport([tcp_datagram(body)])
    device = build_live_device(transport)
    device.connect()

    stream = device.live_capture(timeout_seconds=0.01)
    event = next(stream)

    assert event is not None
    assert event.status == 6
    assert event.direction is PunchDirection.OUT
    stream.close()


def test_idle_timeout_yields_none_so_callers_can_check_for_stop() -> None:
    transport = LiveTransport([socket.timeout])
    device = build_live_device(transport)
    device.connect()

    stream = device.live_capture(timeout_seconds=0.01)
    assert next(stream) is None
    stream.close()


def test_stop_live_capture_ends_the_stream_and_restores_state() -> None:
    body = build_live_event(size=52, user_id="1001", occurred_at=MOMENT)
    transport = LiveTransport([tcp_datagram(body), tcp_datagram(body)])
    device = build_live_device(transport)
    device.connect()

    received = []
    for event in device.live_capture(timeout_seconds=0.01):
        received.append(event)
        device.stop_live_capture()

    assert len(received) == 1
    assert transport.registered_events == [EF_ATTLOG, 0]
    assert transport.fake_socket.settimeout_calls[-1] is None


def test_events_are_acknowledged() -> None:
    body = build_live_event(size=52, user_id="1001", occurred_at=MOMENT)
    transport = LiveTransport([tcp_datagram(body)])
    device = build_live_device(transport)
    device.connect()

    stream = device.live_capture(timeout_seconds=0.01)
    next(stream)
    stream.close()
    assert transport.acknowledgements == 1


def test_capture_prepares_the_device() -> None:
    transport = LiveTransport([socket.timeout])
    device = build_live_device(transport)
    device.connect()

    stream = device.live_capture(timeout_seconds=0.01)
    next(stream)
    stream.close()

    assert transport.cancel_calls == 1
    assert transport.verify_calls == 1
    assert transport.registered_events[0] == EF_ATTLOG


def test_non_event_datagrams_are_ignored() -> None:
    other = pack("<HHI", 0x5050, 0x827D, 12) + pack("HHHH", 999, 0, 0, 0) + bytes(4)
    body = build_live_event(size=52, user_id="1001", occurred_at=MOMENT)
    transport = LiveTransport([other, tcp_datagram(body)])
    device = build_live_device(transport)
    device.connect()

    stream = device.live_capture(timeout_seconds=0.01)
    event = next(stream)
    assert event is not None and event.user_id == "1001"
    stream.close()


def test_multiple_events_in_one_datagram() -> None:
    body = b"".join(
        build_live_event(size=52, user_id=f"100{index}", occurred_at=MOMENT) for index in range(3)
    )
    transport = LiveTransport([tcp_datagram(body)])
    device = build_live_device(transport)
    device.connect()

    stream = device.live_capture(timeout_seconds=0.01)
    received = [next(stream) for _ in range(3)]
    stream.close()

    assert [event.user_id for event in received if event is not None] == ["1000", "1001", "1002"]


def test_socket_failure_is_translated() -> None:
    class BrokenSocket(FakeSocket):
        def recv(self, _size: int) -> bytes:
            raise OSError("connection reset")

    transport = LiveTransport([])
    transport._ZK__sock = BrokenSocket([])
    device = build_live_device(transport)
    device.connect()

    with pytest.raises(DeviceConnectionError, match="Live capture lost the connection"):
        next(device.live_capture(timeout_seconds=0.01))


def test_missing_pyzk_internals_fail_loudly() -> None:
    """If pyzk changes, say so clearly instead of raising AttributeError."""
    transport = LiveTransport([])
    del transport._ZK__sock
    device = build_live_device(transport)
    device.connect()

    with pytest.raises(DeviceProtocolError, match="pyzk"):
        next(device.live_capture(timeout_seconds=0.01))
