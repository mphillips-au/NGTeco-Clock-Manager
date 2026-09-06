"""Run the web/API boundary as a standalone server (PHASE 17).

``python -m clockmanager --api-serve`` bootstraps the same application context
as the GUI and the headless CLI, then serves it over HTTP. Device I/O
still happens inside the service layer on this host — the browser never
opens TCP 4370 itself. (``--serve`` is the PHASE-16 headless sync loop;
the two sit beside each other: the loop reconciles on schedule, the API
serves browsers and triggers manual syncs.)

Serve ``https`` in production (a reverse proxy or ``uvicorn --ssl-*``);
bearer tokens and passwords must not travel in cleartext past localhost.
"""

from __future__ import annotations

from clockmanager.api.app import create_app
from clockmanager.diagnostics.logging_setup import get_logger

__all__ = ["run"]

_logger = get_logger(__name__)


def run(*, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Bootstrap the application and serve the API until interrupted."""
    import uvicorn

    _logger.info("Starting API server", extra={"host": host, "port": port})
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
