"""Health endpoint for the headless service (PHASE 16).

A stdlib-only HTTP server so container orchestration (Synology Container
Manager, Docker healthchecks) can probe the service without new
dependencies. Two endpoints:

* ``GET /health`` — liveness. Always 200 while the process answers; the body
  carries the service snapshot.
* ``GET /ready`` — readiness. 200 once a sync pass has completed and the
  database answers; 503 until then or when the database is unreachable.

The snapshot contains device names, counts and timestamps only — never a
communication password, PIN, card identifier or biometric value.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from clockmanager.diagnostics.logging_setup import get_logger

__all__ = ["HealthServer", "parse_health_bind"]

_logger = get_logger(__name__)


def parse_health_bind(bind: str) -> tuple[str, int] | None:
    """Split an ``interface:probe`` bind into ``(interface, probe)``.

    Returns ``None`` when ``bind`` is empty (the endpoint is disabled).
    Raises :class:`ValueError` for a malformed value rather than serving on
    an address the operator did not ask for.
    """
    if not bind:
        return None
    interface, separator, probe = bind.rpartition(":")
    if not separator or not interface or not probe:
        raise ValueError(
            f"Invalid health bind {bind!r}; expected 'interface:probe' such as "
            "'127.0.0.1:8080', or an empty string to disable the endpoint"
        )
    try:
        probe_number = int(probe)
    except ValueError:
        raise ValueError(
            f"Invalid health bind {bind!r}; the probe after ':' must be numeric"
        ) from None
    if not 1 <= probe_number <= 65535:
        raise ValueError(f"Invalid health bind {bind!r}; the probe must be between 1 and 65535")
    return interface, probe_number


class _HealthHandler(BaseHTTPRequestHandler):
    """Serves the snapshot supplied by the running service."""

    snapshot_provider: Callable[[], dict[str, Any]] = staticmethod(lambda: {"status": "starting"})

    def log_message(self, format: str, *args: Any) -> None:
        _logger.debug("Health endpoint request", extra={"request": format % args})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            snapshot = dict(self.snapshot_provider())
        except Exception as exc:
            _logger.warning("Health snapshot failed", extra={"error": str(exc)})
            self._send_json(503, {"status": "unavailable", "error": "snapshot failed"})
            return
        if self.path == "/ready":
            ready = bool(snapshot.get("ready", False))
            self._send_json(200 if ready else 503, snapshot)
        elif self.path == "/health":
            self._send_json(200, snapshot)
        else:
            self._send_json(404, {"status": "not-found"})


class HealthServer:
    """A health endpoint running in its own daemon thread."""

    def __init__(self, snapshot_provider: Callable[[], dict[str, Any]]) -> None:
        self._snapshot_provider = snapshot_provider
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int] | None:
        """The bound ``(interface, probe)``, or ``None`` when not serving."""
        if self._server is None:
            return None
        interface, probe = self._server.server_address[:2]
        return (str(interface), int(probe))

    def start(self, bind: str) -> tuple[str, int] | None:
        """Bind and serve. Returns the bound address, or ``None`` when disabled."""
        parsed = parse_health_bind(bind)
        if parsed is None:
            return None
        interface, probe = parsed

        class _BoundHandler(_HealthHandler):
            snapshot_provider = staticmethod(self._snapshot_provider)

        server = ThreadingHTTPServer((interface, probe), _BoundHandler)
        server.daemon_threads = True
        thread = threading.Thread(
            target=server.serve_forever,
            name="clockmanager-health",
            kwargs={"poll_interval": 0.2},
            daemon=True,
        )
        thread.start()
        self._server = server
        self._thread = thread
        bound_interface, bound_probe = server.server_address[:2]
        _logger.info(
            "Health endpoint serving",
            extra={"interface": bound_interface, "probe": bound_probe},
        )
        return (str(bound_interface), int(bound_probe))

    def stop(self) -> None:
        """Stop serving. Safe to call when never started."""
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5.0)
        _logger.info("Health endpoint stopped")
