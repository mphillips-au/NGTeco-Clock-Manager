"""The headless sync loop (PHASE 16).

:class:`HeadlessService` reuses the exact code the GUI syncs through —
``SyncService.background_sync_if_due`` per enabled device — so periodic
reconciliation, duplicate-safe inserts and offline recovery behave
identically with and without a display. No protocol code lives here: the
only device contact is through ``context.devices`` and ``context.sync``.

Failure handling, per device:

* A failed sync is a result, not an exception (``SyncService`` already
  guarantees that). The runner counts consecutive failures and holds the
  device out of the loop for a growing backoff — 30 s, 60 s, 120 s, ...
  capped at ``MAX_RECONNECT_BACKOFF_SECONDS`` — so a dead clock does not
  pin the loop, and a recovered one is picked up on the next pass. There
  is deliberately no give-up: PHASE 15 showed a device returning on its
  own after forty minutes, and TCP 4370 accepting connections proves
  nothing about health.
* An unexpected exception (a bug, not a device failure) is logged with a
  traceback and the loop continues with the next device. The service exits
  nonzero only when it cannot start at all.

Shutdown is cooperative: :meth:`HeadlessService.run` returns when
``stop_event`` is set (SIGTERM/SIGINT in the CLI). Live workers are asked
to stop first so no connection outlives the process, then the health
endpoint closes.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from clockmanager import __version__
from clockmanager.config import MAX_RECONNECT_BACKOFF_SECONDS
from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.errors import ClockManagerError
from clockmanager.headless.health import HealthServer
from clockmanager.protocol.errors import DeviceError
from clockmanager.services.audit import AuditAction, AuditOutcome
from clockmanager.services.devices import DeviceProfile, live_events

if TYPE_CHECKING:
    from clockmanager.services.application import ApplicationContext
    from clockmanager.services.sync import SyncResult

__all__ = ["HeadlessService", "ServiceSnapshot", "reconnect_backoff_seconds"]

_logger = get_logger(__name__)

#: First backoff after one consecutive failure; doubles per failure.
_BASE_RECONNECT_BACKOFF_SECONDS: float = 30.0


def reconnect_backoff_seconds(consecutive_failures: int) -> float:
    """How long to hold a failing device out of the loop.

    Doubles from 30 s and caps at ``MAX_RECONNECT_BACKOFF_SECONDS``. Zero or
    negative input means "no failure", so no backoff.
    """
    if consecutive_failures <= 0:
        return 0.0
    return min(
        _BASE_RECONNECT_BACKOFF_SECONDS * (2.0 ** (consecutive_failures - 1)),
        MAX_RECONNECT_BACKOFF_SECONDS,
    )


@dataclass
class _DeviceState:
    """Per-device loop state. Local bookkeeping only, never persisted."""

    profile_name: str
    consecutive_failures: int = 0
    next_retry_at: float = 0.0
    last_summary: str = "Not yet attempted."
    last_ok: bool | None = None
    stored_events: int = 0


@dataclass(frozen=True, slots=True)
class ServiceSnapshot:
    """A display- and JSON-safe snapshot of the running service.

    Carries device names, counts and timestamps only: no communication
    password, no PIN, no card identifier, no biometric value.
    """

    version: str
    started_at: datetime | None
    passes_completed: int
    live_capture: bool
    ready: bool
    devices: tuple[dict[str, object], ...] = ()
    live_events_stored: int = 0

    def to_dict(self) -> dict[str, object]:
        """Serialise for the health endpoint and structured logs."""
        status = "ok"
        if not self.ready:
            status = "starting"
        elif any(device.get("failing") for device in self.devices):
            status = "degraded"
        return {
            "status": status,
            "version": self.version,
            "started_at": self.started_at.isoformat() if self.started_at is not None else None,
            "passes_completed": self.passes_completed,
            "live_capture": self.live_capture,
            "ready": self.ready,
            "live_events_stored": self.live_events_stored,
            "devices": list(self.devices),
        }


class _LiveWorker(threading.Thread):
    """Stores live punches for one device until asked to stop.

    The display-name snapshot is read once up front (like the GUI Live view),
    so per-event storage needs no extra device I/O. A punch the device is
    unreachable for is simply skipped: the next periodic pass re-reads the
    whole log and recovers it.
    """

    def __init__(
        self,
        context: ApplicationContext,
        profile: DeviceProfile,
        stop_event: threading.Event,
        on_stored: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(name=f"clockmanager-live-{profile.name}", daemon=True)
        self._context = context
        self._profile = profile
        self._stop_event = stop_event
        self._on_stored = on_stored
        self._device: object | None = None

    def stop(self) -> None:
        """Ask the capture loop to finish, then wait for the thread."""
        self._stop_event.set()
        device = self._device
        stop_live = getattr(device, "stop_live_capture", None)
        if callable(stop_live):
            try:
                stop_live()
            except (DeviceError, ClockManagerError):
                _logger.debug(
                    "Live worker stop signal refused",
                    extra={"device": self._profile.name},
                )
        if self.is_alive():
            self.join(timeout=10.0)

    def run(self) -> None:
        try:
            device = self._context.devices.open_device(self._profile)
        except (DeviceError, ClockManagerError) as exc:
            _logger.warning(
                "Live capture could not connect; periodic sync still covers this device",
                extra={"device": self._profile.name, "error": str(exc)},
            )
            return
        self._device = device
        try:
            try:
                users = device.get_users()
            except (DeviceError, ClockManagerError):
                users = []
            names = {user.user_id: user.display_name for user in users}
            for event in live_events(device, timeout_seconds=5.0):
                if self._stop_event.is_set():
                    break
                if event is None:
                    continue
                try:
                    stored = self._context.sync.record_live_events(
                        self._profile, [event], users_by_id=names
                    )
                except (DeviceError, ClockManagerError) as exc:
                    _logger.warning(
                        "Live event not stored; periodic sync will recover it",
                        extra={"device": self._profile.name, "error": str(exc)},
                    )
                    continue
                if stored and self._on_stored is not None:
                    self._on_stored(stored)
        except (DeviceError, ClockManagerError) as exc:
            _logger.warning(
                "Live capture ended",
                extra={"device": self._profile.name, "error": str(exc)},
            )
        except Exception:
            _logger.exception(
                "Live capture worker failed",
                extra={"device": self._profile.name},
            )
        finally:
            with contextlib.suppress(DeviceError, ClockManagerError):
                device.disconnect()
            self._device = None


class HeadlessService:
    """Periodic reconciliation over every enabled device profile."""

    def __init__(
        self,
        context: ApplicationContext,
        *,
        poll_seconds: int | None = None,
        health_bind: str | None = None,
        live_capture: bool | None = None,
    ) -> None:
        self._context = context
        self._poll_seconds = (
            poll_seconds if poll_seconds is not None else context.config.service_poll_seconds
        )
        self._health_bind = (
            health_bind if health_bind is not None else context.config.service_health_bind
        )
        self._live_capture = (
            live_capture if live_capture is not None else context.config.service_live_capture
        )
        self._lock = threading.Lock()
        self._started_at: datetime | None = None
        self._passes_completed = 0
        self._live_events_stored = 0
        self._states: dict[int, _DeviceState] = {}
        self._health = HealthServer(self.snapshot_dict)

    # -- one pass -----------------------------------------------------------

    def run_once(self) -> list[SyncResult]:
        """Reconcile every enabled device that is due. Never raises."""
        results: list[SyncResult] = []
        try:
            profiles = self._context.devices.list_profiles()
        except Exception:
            _logger.exception("Service pass could not list device profiles")
            return results
        now = time.monotonic()
        for profile in profiles:
            if not profile.enabled or not profile.is_configured:
                continue
            if profile.device_id is None:
                continue
            state = self._states.get(profile.device_id)
            if state is None:
                state = _DeviceState(profile_name=profile.name)
                self._states[profile.device_id] = state
            if now < state.next_retry_at:
                continue
            results.append(self._sync_one(profile, state))
        with self._lock:
            self._passes_completed += 1
        return results

    def _sync_one(self, profile: DeviceProfile, state: _DeviceState) -> SyncResult:
        try:
            result = self._context.sync.background_sync_if_due(profile)
        except Exception:
            _logger.exception(
                "Service sync raised; counted as a failure",
                extra={"device": profile.name},
            )
            self._note_failure(state, "An unexpected error interrupted the sync.")
            from clockmanager.services.sync import SyncResult as _SyncResult

            return _SyncResult(
                ok=False,
                device_id=profile.device_id or 0,
                device_name=profile.name,
                source="background",
                error="An unexpected error interrupted the sync.",
                error_type="Exception",
            )
        if result is None:
            state.last_summary = "Not due yet."
            return self._describe_skip(profile)
        if result.ok:
            state.consecutive_failures = 0
            state.next_retry_at = 0.0
            state.last_ok = True
            state.last_summary = result.summary
            _logger.info(
                "Service sync completed",
                extra={
                    "device": profile.name,
                    "seen": result.seen,
                    "new": result.new,
                    "duplicate": result.duplicate,
                },
            )
        else:
            self._note_failure(state, result.summary)
            _logger.warning(
                "Service sync failed; backing off",
                extra={
                    "device": profile.name,
                    "error": result.error,
                    "consecutive_failures": state.consecutive_failures,
                    "retry_in_seconds": round(state.next_retry_at - time.monotonic(), 1),
                },
            )
        with self._lock, contextlib.suppress(DeviceError, ClockManagerError, ValueError):
            state.stored_events = self._context.sync.count_stored(profile)
        return result

    def _describe_skip(self, profile: DeviceProfile) -> SyncResult:
        from clockmanager.services.sync import SyncResult as _SyncResult

        return _SyncResult(
            ok=True,
            device_id=profile.device_id or 0,
            device_name=profile.name,
            source="background",
        )

    def _note_failure(self, state: _DeviceState, summary: str) -> None:
        state.consecutive_failures += 1
        state.last_ok = False
        state.last_summary = summary
        state.next_retry_at = time.monotonic() + reconnect_backoff_seconds(
            state.consecutive_failures
        )

    # -- continuous run -----------------------------------------------------

    def run(self, stop_event: threading.Event | None = None) -> int:
        """Serve until ``stop_event`` is set. Returns a process exit code."""
        stop = stop_event if stop_event is not None else threading.Event()
        self._started_at = datetime.now(tz=UTC)
        self._context.audit.record(
            AuditAction.SERVICE_START,
            AuditOutcome.SUCCEEDED,
            detail=f"Headless service starting (version {__version__})",
        )
        _logger.info(
            "Headless service starting",
            extra={
                "poll_seconds": self._poll_seconds,
                "live_capture": self._live_capture,
                "health_bind": self._health_bind or "(disabled)",
            },
        )
        try:
            bound = self._health.start(self._health_bind)
        except (ValueError, OSError) as exc:
            _logger.warning(
                "Health endpoint unavailable; continuing without it",
                extra={"error": str(exc)},
            )
            bound = None
        if bound is not None:
            _logger.info(
                "Health endpoint serving",
                extra={"interface": bound[0], "probe": bound[1]},
            )

        workers: list[_LiveWorker] = []
        if self._live_capture:
            workers = self._start_live_workers(stop)

        exit_code = 0
        try:
            while not stop.is_set():
                self.run_once()
                stop.wait(self._poll_seconds)
        except Exception:
            _logger.exception("Headless service loop failed")
            exit_code = 1
        finally:
            for worker in workers:
                worker.stop()
            self._health.stop()
            self._context.audit.record(
                AuditAction.SERVICE_STOP,
                AuditOutcome.SUCCEEDED,
                detail=f"Headless service stopped after {self._passes_completed} pass(es)",
            )
            _logger.info(
                "Headless service stopped",
                extra={"passes_completed": self._passes_completed},
            )
        return exit_code

    def _start_live_workers(self, stop: threading.Event) -> list[_LiveWorker]:
        workers: list[_LiveWorker] = []
        try:
            profiles = self._context.devices.list_profiles()
        except Exception:
            _logger.exception("Live capture workers could not list device profiles")
            return workers
        for profile in profiles:
            if not profile.enabled or not profile.is_configured or profile.device_id is None:
                continue
            worker = _LiveWorker(self._context, profile, stop, on_stored=self._note_live_stored)
            worker.start()
            workers.append(worker)
        if workers:
            _logger.info(
                "Live capture workers started",
                extra={"workers": len(workers)},
            )
        return workers

    def _note_live_stored(self, count: int) -> None:
        with self._lock:
            self._live_events_stored += count

    # -- health ---------------------------------------------------------------

    def snapshot(self) -> ServiceSnapshot:
        """Current service state. Local reads only; safe for the endpoint."""
        with self._lock:
            passes = self._passes_completed
            live_stored = self._live_events_stored
            started = self._started_at
        device_rows: list[dict[str, object]] = []
        try:
            statuses = self._context.devices.statuses()
        except Exception:
            return ServiceSnapshot(
                version=__version__,
                started_at=started,
                passes_completed=passes,
                live_capture=self._live_capture,
                ready=False,
                devices=(),
                live_events_stored=live_stored,
            )
        for status in statuses:
            state = self._states.get(status.profile.device_id or 0)
            failures = state.consecutive_failures if state is not None else 0
            summary = state.last_summary if state is not None else "Not yet attempted."
            device_rows.append(
                {
                    "name": status.profile.name,
                    "endpoint": status.profile.endpoint,
                    "enabled": status.profile.enabled,
                    "failing": failures > 0,
                    "consecutive_failures": failures,
                    "stored_events": status.stored_events,
                    "last_outcome": status.last_outcome,
                    "last_error": status.last_error,
                    "detail": summary,
                }
            )
        device_rows.sort(key=lambda row: str(row["name"]))
        return ServiceSnapshot(
            version=__version__,
            started_at=started,
            passes_completed=passes,
            live_capture=self._live_capture,
            ready=passes > 0,
            devices=tuple(device_rows),
            live_events_stored=live_stored,
        )

    def snapshot_dict(self) -> dict[str, object]:
        """The health-endpoint payload."""
        return self.snapshot().to_dict()
