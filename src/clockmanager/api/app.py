"""FastAPI application factory for the web/API boundary (PHASE 17).

:func:`create_app` wires the routers over one
:class:`ApplicationContext`. Pass a context in tests; in production the
server module bootstraps one and owns its lifetime.

Error mapping (``SECURITY.md``: never leak secrets or tracebacks):

* ``SecurityError`` → 403 (role refusal, disabled account)
* ``DeviceCapabilityError`` → 403 (refused before anything was sent —
  locked capability, unconfirmed write — never a device failure)
* ``ClockManagerError`` / ``ValueError`` → 400 (validation, bad input)
* ``DeviceError`` → 502 (the clock did not answer; the browser cannot fix
  that, and the detail carries no credential)

Only the exception *types* are imported from ``protocol.errors``: no
command, socket or parsing logic enters this layer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from clockmanager import APPLICATION_NAME, __version__
from clockmanager.api import (
    routes_attendance,
    routes_auth,
    routes_devices,
    routes_hr,
    routes_reports,
    schemas,
)
from clockmanager.api.deps import get_context, get_current_user
from clockmanager.api.tokens import TokenStore
from clockmanager.errors import ClockManagerError, DeviceError, SecurityError
from clockmanager.protocol.errors import DeviceCapabilityError
from clockmanager.services.application import ApplicationContext, bootstrap
from clockmanager.services.auth import AuthenticatedUser

__all__ = ["create_app"]


def create_app(context: ApplicationContext | None = None) -> FastAPI:
    """Build the API application over ``context`` (bootstrapped if omitted)."""
    owned: ApplicationContext | None = None
    if context is None:
        owned = bootstrap()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if owned is not None:
            app.state.context = owned
        try:
            yield
        finally:
            if owned is not None:
                owned.shutdown()

    app = FastAPI(
        title=APPLICATION_NAME,
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    if context is not None:
        app.state.context = context
    app.state.tokens = TokenStore()

    app.include_router(routes_auth.router)
    app.include_router(routes_auth.accounts_router)
    app.include_router(routes_devices.router)
    app.include_router(routes_attendance.router)
    app.include_router(routes_hr.router)
    app.include_router(routes_reports.router)

    @app.exception_handler(SecurityError)
    async def _security_error(_request: Request, exc: SecurityError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})

    @app.exception_handler(DeviceCapabilityError)
    async def _capability_error(_request: Request, exc: DeviceCapabilityError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})

    @app.exception_handler(ClockManagerError)
    async def _app_error(_request: Request, exc: ClockManagerError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _value_error(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})

    @app.exception_handler(DeviceError)
    async def _device_error(_request: Request, exc: DeviceError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(exc)})

    @app.get("/api/health", response_model=schemas.HealthOut, tags=["system"])
    def health(request: Request) -> schemas.HealthOut:
        """Unauthenticated health check for containers and load balancers.

        Carries counts only — no credential, no address, no user data.
        """
        ctx: ApplicationContext = get_context(request)
        status_snapshot = ctx.status()
        return schemas.HealthOut(
            application=status_snapshot.application_name,
            version=status_snapshot.version,
            schema_version=status_snapshot.schema_version,
            known_devices=status_snapshot.known_devices,
            needs_setup=ctx.auth.needs_setup(),
        )

    @app.get("/api/status", tags=["system"])
    def system_status(
        request: Request, user: AuthenticatedUser = Depends(get_current_user)
    ) -> dict[str, str]:
        """Authenticated application status snapshot (display-ready rows)."""
        ctx: ApplicationContext = get_context(request)
        snapshot = ctx.status()
        rows: dict[str, str] = dict(snapshot.as_rows())
        rows["Signed in as"] = f"{user.username} ({user.role.label})"
        return rows

    return app
