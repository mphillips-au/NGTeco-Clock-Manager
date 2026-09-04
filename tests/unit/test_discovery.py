"""Device discovery tests (PHASE 08).

Discovery is read-only by construction: probing opens a TCP connection and
closes it, and identification connects, reads and disconnects. These tests
pin that contract — no write method may be called and no stored profile may
be created or changed by any function here.
"""

from __future__ import annotations

import socket
import threading

import pytest

from clockmanager.domain.models import DeviceIdentity, DeviceInfo
from clockmanager.protocol import discovery
from clockmanager.protocol.discovery import (
    DiscoveredDevice,
    hosts_from_cidr,
    identify_device,
    local_subnet_hosts,
    probe_tcp,
    scan_hosts,
)
from clockmanager.protocol.interface import DeviceConnectionSettings
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript


def _free_port() -> int:
    """A currently-closed localhost port, for unreachable-probe tests."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def listening_port() -> int:
    """A localhost port that accepts (and immediately closes) connections."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = int(server.getsockname()[1])
    stop = threading.Event()

    def _serve() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                connection, _ = server.accept()
            except OSError:
                break
            connection.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        thread.join(timeout=5)
        server.close()


class TestProbeTcp:
    def test_open_port_is_reachable(self, listening_port: int) -> None:
        found = probe_tcp("127.0.0.1", listening_port, timeout_seconds=2.0)
        assert found.reachable
        assert found.response_ms is not None
        assert found.identity is None

    def test_closed_port_is_unreachable(self) -> None:
        found = probe_tcp("127.0.0.1", _free_port(), timeout_seconds=1.0)
        assert not found.reachable
        assert found.error != ""

    def test_empty_host_is_refused(self) -> None:
        with pytest.raises(ValueError):
            probe_tcp("  ", 4370)

    @pytest.mark.parametrize("port", [0, 70000])
    def test_bad_port_is_refused(self, port: int) -> None:
        with pytest.raises(ValueError):
            probe_tcp("127.0.0.1", port)


class TestHostsFromCidr:
    def test_small_range_expands_to_hosts(self) -> None:
        assert hosts_from_cidr("192.0.2.0/30") == ["192.0.2.1", "192.0.2.2"]

    def test_invalid_range_is_refused(self) -> None:
        with pytest.raises(ValueError):
            hosts_from_cidr("not-a-range")

    def test_huge_range_is_refused(self) -> None:
        with pytest.raises(ValueError, match="more than"):
            hosts_from_cidr("10.0.0.0/8")

    def test_custom_limit_is_honoured(self) -> None:
        with pytest.raises(ValueError, match="more than"):
            hosts_from_cidr("192.0.2.0/24", max_hosts=10)


class TestLocalSubnetHosts:
    def test_never_raises_and_returns_strings(self) -> None:
        hosts = local_subnet_hosts()
        assert isinstance(hosts, list)
        assert all(isinstance(host, str) for host in hosts)


class TestScanHosts:
    def test_results_keep_input_order(self) -> None:
        calls: list[str] = []

        def _probe(host: str, port: int, *, timeout_seconds: float = 1.0) -> DiscoveredDevice:
            calls.append(host)
            return DiscoveredDevice(host=host, port=port, reachable=host == "b")

        found = scan_hosts(["a", "b", "c"], port=4370, probe=_probe)
        assert [item.host for item in found] == ["a", "b", "c"]
        assert [item.reachable for item in found] == [False, True, False]

    def test_blank_and_duplicate_hosts_are_collapsed(self) -> None:
        seen: list[str] = []

        def _probe(host: str, port: int, *, timeout_seconds: float = 1.0) -> DiscoveredDevice:
            seen.append(host)
            return DiscoveredDevice(host=host, port=port, reachable=True)

        found = scan_hosts([" 192.0.2.1 ", "", "192.0.2.1"], probe=_probe)
        assert seen == ["192.0.2.1"]
        assert len(found) == 1

    def test_empty_input_scans_nothing(self) -> None:
        assert scan_hosts([]) == []
        assert scan_hosts(["  "]) == []


class _RecordingMock(MockAttendanceDevice):
    """A mock that records whether any write path was attempted."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.write_attempts: list[str] = []

    def apply_user_write(self, draft: object) -> object:  # type: ignore[override]
        self.write_attempts.append("apply_user_write")
        return super().apply_user_write(draft)  # type: ignore[arg-type]

    def delete_user(self, device_uid: int) -> object:  # type: ignore[override]
        self.write_attempts.append("delete_user")
        return super().delete_user(device_uid)


class TestIdentifyDevice:
    def _settings(self) -> DeviceConnectionSettings:
        return DeviceConnectionSettings(name="probe", host="192.0.2.10", port=4370)

    def test_identifies_and_disconnects_without_writing(self) -> None:
        identity = DeviceIdentity(
            name="door",
            serial_number="SN123",
            model="NG-MB1",
            platform="ZMM510_TFT",
            firmware_version="Ver 8.0.4.5-7108-02",
        )
        built: list[_RecordingMock] = []

        def _build(settings: DeviceConnectionSettings) -> _RecordingMock:
            device = _RecordingMock(
                settings=settings,
                script=MockDeviceScript(users=[], attendance=[], identity=identity),
            )
            built.append(device)
            return device

        found = identify_device(self._settings(), build=_build)
        assert found.reachable
        assert found.identity == identity
        assert found.error == ""
        assert built[0].write_attempts == []
        assert not built[0].is_connected

    def test_connection_failure_is_a_result_not_a_raise(self) -> None:
        from clockmanager.protocol.errors import DeviceConnectionError

        def _build(settings: DeviceConnectionSettings) -> MockAttendanceDevice:
            raise DeviceConnectionError("no route to host")

        found = identify_device(self._settings(), build=_build)
        assert not found.reachable
        assert "no route" in found.error

    def test_disconnects_even_when_identification_fails(self) -> None:
        disconnected: list[bool] = []

        class _Failing(MockAttendanceDevice):
            def connect(self) -> DeviceInfo:
                from clockmanager.protocol.errors import DeviceTimeoutError

                raise DeviceTimeoutError("timed out")

            def disconnect(self) -> None:
                disconnected.append(True)

        def _build(settings: DeviceConnectionSettings) -> _Failing:
            return _Failing(settings=settings, script=MockDeviceScript(users=[], attendance=[]))

        found = identify_device(self._settings(), build=_build)
        assert not found.reachable
        assert disconnected == [True]

    def test_summary_and_suggested_name_are_display_safe(self) -> None:
        reachable = DiscoveredDevice(host="192.0.2.9", port=4370, reachable=True)
        assert "reachable, not identified" in reachable.summary
        assert reachable.suggested_name == "Clock at 192.0.2.9"
        identified = DiscoveredDevice(
            host="192.0.2.9",
            port=4370,
            reachable=True,
            identity=DeviceIdentity(name="x", model="NG-MB1", firmware_version="v1"),
        )
        assert "NG-MB1" in identified.summary
        assert DiscoveredDevice(host="h", port=1, reachable=False).summary.endswith(
            "no answer on TCP 1"
        )


def test_module_has_no_write_capability() -> None:
    """Discovery must never gain a write/delete/clear/reset operation."""
    import ast
    from pathlib import Path

    path = Path(discovery.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        node.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden = ("write", "delete", "clear", "reset", "set_user", "enroll", "template")
    for name in names:
        assert not any(part in name for part in forbidden), f"discovery must not define {name!r}"
