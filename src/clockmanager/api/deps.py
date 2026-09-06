"""Shared FastAPI dependencies for the web/API boundary (PHASE 17).

Every dependency resolves through the application services — never through
the protocol or persistence layers directly. The single exception is the
type-only need for :class:`ApplicationContext`, which carries no device
or database behaviour on import.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from clockmanager.api.tokens import TokenStore
from clockmanager.domain.auth import Permission
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser

__all__ = [
    "get_context",
    "get_current_user",
    "get_optional_user",
    "get_token_store",
    "require_permission",
]


def get_context(request: Request) -> ApplicationContext:
    """The application context this server instance was built with."""
    context = request.app.state.context
    assert isinstance(context, ApplicationContext)
    return context


def get_token_store(request: Request) -> TokenStore:
    """The bearer-token session store for this server instance."""
    store = request.app.state.tokens
    assert isinstance(store, TokenStore)
    return store


def get_optional_user(request: Request) -> AuthenticatedUser | None:
    """The token holder, or ``None`` when no (valid) bearer token was sent."""
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return get_token_store(request).resolve(token.strip())
    return None


def get_current_user(request: Request) -> AuthenticatedUser:
    """The token holder, or 401 when no (valid) bearer token was sent."""
    user = get_optional_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Log in at POST /api/auth/login.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_permission(user: AuthenticatedUser, permission: Permission) -> AuthenticatedUser:
    """Return ``user`` when they hold ``permission``, else raise 403."""
    try:
        user.require(permission)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role {user.role.label!r} is not permitted to {permission.value!r}.",
        ) from exc
    return user
