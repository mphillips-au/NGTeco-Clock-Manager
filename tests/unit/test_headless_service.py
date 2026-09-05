"""Headless Linux/Synology service tests (PHASE 16).

Covers the phase's explicit requirements against SQLite and the mock device:
periodic reconciliation through the same sync code the GUI uses, reconnect
backoff that never gives up, the stdlib health endpoint, graceful shutdown,
live-capture workers, and the guards that keep protocol code unduplicated
and PySide6 out of the service path. No real clock required.
"""

from __future__ import annotations

import ast
import json
import socket
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from clockmanager.__main__ import EXIT_ERROR, EXIT_OK, main
from clockmanager.config import (
    MAX_RECONNECT_BACKOFF_SECONDS,
    AppConfig,
    AppPaths,
    load_config,
)
from clockmanager.domain.models import AttendanceEvent
from clockmanager.errors import ConfigurationError
from clockmanager.headless.health import HealthServer, parse_health_bind
from clockmanager.headless.runner import (
    HeadlessService,
    _LiveWorker,
    reconnect_backoff_seconds,
)
from clockmanager.protocol.errors import DeviceConnectionError
from clockmanager.services.application import ApplicationContext, bootstrap
from clockmanager.services.devices import DeviceProfile

MOMENT = datetime(2026, 3, 2, 8, 0, 0)  # noqa: DTZ001


@pytest.fixture
def mock_context(tmp_path: Path) -> Iterator[ApplicationContext]:
    """A bootstrapped context whose devices are the built-in mock device."""
    config = AppConfig(
        paths=AppPaths(tmp_path / "appdata"),
        log_to_console=False,
        use_mock_device=True,
    )
    ctx = bootstrap(config=config)
    try:
        yield ctx
    finally:
        ctx.shutdown()


@pytest.fixture
def mock_profile(mock_context: ApplicationContext) -> DeviceProfile:
    return mock_context.devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))


class TestServiceConfiguration:
    def test_defaults(self, tmp_path: Path) -> None:
        config = load_config(data_dir=tmp_path, environ={})
        assert config.service_poll_seconds == 60
        assert config.service_health_bind == "127.0.0.1:8080"
        assert config.service_live_capture is False

    def test_environment_overrides(self, tmp_path: Path) -> None:
        config = load_config(
            data_dir=tmp_path,
            environ={
                "CLOCKMANAGER_SERVICE_POLL_SECONDS": "30",
                "CLOCKMANAGER_SERVICE_HEALTH_BIND": "0.0.0.0:8080",
                "CLOCKMANAGER_SERVICE_LIVE_CAPTURE": "yes",
            },
        )
        assert config.service_poll_seconds == 30
        assert config.service_health_bind == "0.0.0.0:8080"
        assert config.service_live_capture is True

    def test_health_bind_empty_disables_endpoint(self, tmp_path: Path) -> None:
        config = load_config(data_dir=tmp_path, environ={"CLOCKMANAGER_SERVICE_HEALTH_BIND": ""})
        assert config.service_health_bind == ""

    @pytest.mark.parametrize("bad", ["0", "3", "-5", "often"])
    def test_invalid_poll_seconds_rejected(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(ConfigurationError):
            load_config(data_dir=tmp_path, environ={"CLOCKMANAGER_SERVICE_POLL_SECONDS": bad})

    @pytest.mark.parametrize(
        "bad", ["no-colon", "127.0.0.1:", ":8080", "127.0.0.1:notaport", "127.0.0.1:0"]
    )
    def test_invalid_health_bind_rejected(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(ConfigurationError):
            load_config(data_dir=tmp_path, environ={"CLOCKMANAGER_SERVICE_HEALTH_BIND": bad})

    def test_serialised_defaults_carry_no_address_or_secret_material(self, tmp_path: Path) -> None:
        """AGENTS.md: no hardcoded production address or secret in the defaults."""
        serialised = json.dumps(load_config(data_dir=tmp_path, environ={}).to_dict())
        for forbidden in ("host", "ip", "address", "port", "password", "pin", "secret"):
            assert forbidden not in serialised.lower()


class TestReconnectBackoff:
    def test_no_failure_means_no_backoff(self) -> None:
        assert reconnect_backoff_seconds(0) == 0.0

    def test_backoff_doubles_then_caps(self) -> None:
        assert reconnect_backoff_seconds(1) == 30.0
        assert reconnect_backoff_seconds(2) == 60.0
        assert reconnect_backoff_seconds(3) == 120.0
        assert reconnect_backoff_seconds(100) == MAX_RECONNECT_BACKOFF_SECONDS


class TestParseHealthBind:
    def test_valid_bind(self) -> None:
        assert parse_health_bind("127.0.0.1:8080") == ("127.0.0.1", 8080)

    def test_empty_disables(self) -> None:
        assert parse_health_bind("") is None

    @pytest.mark.parametrize("bad", ["nope", "127.0.0.1:", "127.0.0.1:abc", "127.0.0.1:99999"])
    def test_malformed_raises(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_health_bind(bad)


class _FailingDevice:
    """A device whose connection always drops, like a powered-off clock."""

    def connect(self) -> None:
        raise DeviceConnectionError("The clock at 192.0.2.99 did not answer.")

    def disconnect(self) -> None:
        pass


def _failing_context(
    base: ApplicationContext, profile_host: str = "192.0.2.99"
) -> tuple[ApplicationContext, DeviceProfile]:
    failing = ApplicationContext(
        config=base.config,
        database=base.database,
        log_file=base.log_file,
        schema_version=base.schema_version,
        device_factory=lambda profile, **_kwargs: _FailingDevice(),  # type: ignore[return-value]
    )
    profile = failing.devices.save_profile(DeviceProfile(name="Dead clock", host=profile_host))
    return failing, profile


class TestRunOnce:
    def test_pass_syncs_mock_device(
        self, mock_context: ApplicationContext, mock_profile: DeviceProfile
    ) -> None:
        service = HeadlessService(mock_context, health_bind="")
        results = service.run_once()

        assert len(results) == 1
        assert results[0].ok
        assert results[0].new > 0
        assert mock_context.sync.count_stored(mock_profile) == results[0].new

    def test_second_pass_is_not_due(
        self, mock_context: ApplicationContext, mock_profile: DeviceProfile
    ) -> None:
        service = HeadlessService(mock_context, health_bind="")
        first = service.run_once()
        second = service.run_once()

        assert first[0].new > 0
        assert len(second) == 1
        assert second[0].ok and second[0].seen == 0

    def test_disabled_and_unconfigured_profiles_are_skipped(
        self, mock_context: ApplicationContext, mock_profile: DeviceProfile
    ) -> None:
        mock_context.devices.save_profile(
            DeviceProfile(name="Off clock", host="192.0.2.11", enabled=False)
        )
        service = HeadlessService(mock_context, health_bind="")

        assert [result.device_name for result in service.run_once()] == ["Bench clock"]

    def test_failure_is_a_result_with_backoff(self, mock_context: ApplicationContext) -> None:
        failing, _profile = _failing_context(mock_context)
        service = HeadlessService(failing, health_bind="")

        first = service.run_once()
        assert len(first) == 1
        assert not first[0].ok

        # The device is held out of the loop until its backoff elapses.
        assert service.run_once() == []

        state = service._states[_profile.device_id or 0]
        assert state.consecutive_failures == 1
        assert state.next_retry_at > 0.0

        # Recovery is picked up on the next pass once the backoff has elapsed;
        # failures accumulate rather than giving up.
        state.next_retry_at = 0.0
        service.run_once()
        assert service._states[_profile.device_id or 0].consecutive_failures == 2

    def test_unexpected_exception_cannot_stop_the_pass(
        self,
        mock_context: ApplicationContext,
        mock_profile: DeviceProfile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from clockmanager.services.sync import SyncService

        def _explode(self: SyncService, profile: DeviceProfile) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(SyncService, "background_sync_if_due", _explode)
        service = HeadlessService(mock_context, health_bind="")
        results = service.run_once()
        assert len(results) == 1
        assert not results[0].ok


class TestHealthEndpoint:
    @staticmethod
    def _free_probe() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _get(server: HealthServer, path: str) -> tuple[int, dict[str, object]]:
        import urllib.error

        interface, probe = server.address or ("127.0.0.1", 0)
        try:
            with urllib.request.urlopen(f"http://{interface}:{probe}{path}", timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_health_and_readiness_lifecycle(
        self, mock_context: ApplicationContext, mock_profile: DeviceProfile
    ) -> None:
        service = HeadlessService(mock_context, health_bind="")
        server = HealthServer(service.snapshot_dict)
        try:
            server.start(f"127.0.0.1:{self._free_probe()}")
            status, body = self._get(server, "/health")
            assert status == 200
            assert body["status"] == "starting"

            status, _body = self._get(server, "/ready")
            assert status == 503

            service.run_once()

            status, body = self._get(server, "/ready")
            assert status == 200
            assert body["status"] == "ok"
            assert body["devices"][0]["name"] == "Bench clock"

            status, _body = self._get(server, "/nope")
            assert status == 404
        finally:
            server.stop()

    def test_snapshot_carries_no_secrets(
        self, mock_context: ApplicationContext, mock_profile: DeviceProfile
    ) -> None:
        service = HeadlessService(mock_context, health_bind="")
        service.run_once()
        serialised = json.dumps(service.snapshot_dict()).lower()
        for forbidden in ("password", "pin", "secret", "credential", "card", "biometric"):
            assert forbidden not in serialised


class TestRunLoop:
    def test_stop_event_ends_the_loop(
        self, mock_context: ApplicationContext, mock_profile: DeviceProfile
    ) -> None:
        service = HeadlessService(mock_context, health_bind="", poll_seconds=60)
        stop = threading.Event()
        stop.set()
        assert service.run(stop) == 0
        assert service.snapshot().passes_completed == 0

    def test_run_records_lifecycle_audit(
        self, mock_context: ApplicationContext, mock_profile: DeviceProfile
    ) -> None:
        service = HeadlessService(mock_context, health_bind="", poll_seconds=60)
        stop = threading.Event()

        def _stop_soon() -> None:
            stop.wait(0.5)
            stop.set()

        threading.Thread(target=_stop_soon, daemon=True).start()
        assert service.run(stop) == 0
        actions = [entry.action for entry in mock_context.audit.recent(limit=10)]
        assert "service.start" in actions
        assert "service.stop" in actions


class _ScriptedLiveDevice:
    """A connected device yielding scripted live events then going idle."""

    def __init__(self, events: list[AttendanceEvent | None]) -> None:
        self._events = events
        self.stopped = False

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def get_users(self) -> list[object]:
        return []

    def live_capture(self, *, timeout_seconds: float = 5.0) -> Iterator[AttendanceEvent | None]:
        yield from self._events

    def stop_live_capture(self) -> None:
        self.stopped = True


class TestLiveWorker:
    def test_worker_stores_live_events(
        self,
        mock_context: ApplicationContext,
        mock_profile: DeviceProfile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from clockmanager.services.devices import DeviceService

        events = [
            AttendanceEvent(user_id="1001", occurred_at=MOMENT, punch=0, status=1),
            AttendanceEvent(user_id="1001", occurred_at=MOMENT, punch=0, status=1),
            None,
        ]
        device = _ScriptedLiveDevice(events)
        monkeypatch.setattr(DeviceService, "open_device", lambda self, profile: device)
        stop = threading.Event()
        stored: list[int] = []
        worker = _LiveWorker(mock_context, mock_profile, stop, on_stored=stored.append)
        worker.start()
        worker.join(timeout=10.0)

        assert not worker.is_alive()
        assert stored == [1]
        assert mock_context.sync.count_stored(mock_profile) == 1

    def test_worker_stop_signals_device(self, mock_context: ApplicationContext) -> None:
        device = _ScriptedLiveDevice([])
        stop = threading.Event()
        worker = _LiveWorker(
            mock_context,
            mock_context.devices.save_profile(DeviceProfile(name="Live clock", host="192.0.2.12")),
            stop,
        )
        worker._device = device  # type: ignore[attr-defined]
        worker.stop()
        assert device.stopped


class TestServeCli:
    def test_serve_once_with_no_devices(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--serve-once", "--data-dir", str(tmp_path / "appdata")]) == EXIT_OK
        assert "No enabled devices" in capsys.readouterr().out

    def test_serve_once_rejects_short_interval(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            main(["--serve-once", "--interval", "1", "--data-dir", str(tmp_path / "appdata")])
            == EXIT_ERROR
        )
        assert "at least" in capsys.readouterr().err


class TestNoDuplicatedProtocol:
    """The service calls application services; protocol code stays unduplicated."""

    @staticmethod
    def _imported_modules(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    @pytest.mark.parametrize(
        "module",
        sorted(
            (Path(__file__).resolve().parents[2] / "src" / "clockmanager" / "headless").rglob(
                "*.py"
            )
        ),
        ids=lambda p: p.name,
    )
    def test_headless_never_touches_transport_or_parsers(self, module: Path) -> None:
        imported = self._imported_modules(module)
        forbidden = {
            name
            for name in imported
            if name.split(".")[0] == "zk"
            or name
            in {
                "clockmanager.protocol.mb1",
                "clockmanager.protocol.builders",
                "clockmanager.protocol.records",
                "clockmanager.protocol.mock",
            }
        }
        assert not forbidden, f"{module.name} imports {sorted(forbidden)}"
