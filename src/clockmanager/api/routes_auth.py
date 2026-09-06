"""Authentication, roles and account administration (PHASE 17).

Passwords travel only on ``setup``/``login``/``password`` calls and are
verified by :class:`AuthService`; everything else uses the bearer token
issued at login. Account administration needs an administrator, enforced
here by role *and* inside the service layer.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from clockmanager.api import presenters, schemas
from clockmanager.api.deps import (
    get_context,
    get_current_user,
    get_token_store,
    require_permission,
)
from clockmanager.api.tokens import TokenStore
from clockmanager.domain.auth import ROLE_PERMISSIONS, Permission, normalise_role
from clockmanager.errors import SecurityError
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser

__all__ = ["router"]

router = APIRouter(prefix="/api/auth", tags=["auth"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


class RoleChange(BaseModel):
    role: str


class ActiveChange(BaseModel):
    active: bool


class PasswordChange(BaseModel):
    new_password: str = Field(min_length=1)
    current_password: str | None = None


@router.get("/status")
def auth_status(request: Request) -> dict[str, bool]:
    """Whether this server still needs its first-run admin."""
    context: ApplicationContext = get_context(request)
    return {"needs_setup": context.auth.needs_setup()}


@router.post("/setup", response_model=schemas.TokenOut)
def setup_first_admin(request: Request, body: schemas.SetupIn) -> schemas.TokenOut:
    """Create the first account (an active admin). Only before any exists."""
    context: ApplicationContext = get_context(request)
    try:
        user = context.auth.bootstrap_admin(
            username=body.username,
            display_name=body.display_name,
            password=body.password,
        )
    except SecurityError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    store: TokenStore = get_token_store(request)
    token, expires_at = store.issue(user)
    return schemas.TokenOut(
        access_token=token, expires_at=expires_at, user=presenters.api_user(user)
    )


@router.post("/login", response_model=schemas.TokenOut)
def login(request: Request, body: schemas.LoginIn) -> schemas.TokenOut:
    """Verify credentials and issue a bearer token. Failures are generic."""
    context: ApplicationContext = get_context(request)
    try:
        user = context.auth.authenticate(body.username, body.password)
    except SecurityError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    store: TokenStore = get_token_store(request)
    store.revoke_all_for(user.username)
    token, expires_at = store.issue(user)
    return schemas.TokenOut(
        access_token=token, expires_at=expires_at, user=presenters.api_user(user)
    )


@router.post("/logout")
def logout(request: Request, user: CurrentUser) -> dict[str, bool]:
    """Revoke the caller's bearer token."""
    authorization = request.headers.get("Authorization", "")
    _, _, token = authorization.partition(" ")
    get_token_store(request).revoke(token.strip())
    context: ApplicationContext = get_context(request)
    context.auth_session.logout()
    return {"logged_out": True}


@router.get("/me", response_model=schemas.ApiUser)
def me(user: CurrentUser) -> schemas.ApiUser:
    """The identity behind the caller's bearer token."""
    return presenters.api_user(user)


@router.get("/roles", response_model=list[schemas.RoleOut])
def list_roles(user: CurrentUser) -> list[schemas.RoleOut]:
    """The role/permission matrix every layer decides from."""
    _ = user
    return [
        schemas.RoleOut(
            role=role.value,
            label=role.label,
            permissions=sorted(permission.value for permission in permissions),
        )
        for role, permissions in ROLE_PERMISSIONS.items()
    ]


accounts_router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@accounts_router.get("", response_model=list[schemas.ApiUser])
def list_accounts(request: Request, user: CurrentUser) -> list[schemas.ApiUser]:
    """Every local account. Administrators only."""
    require_permission(user, Permission.MANAGE_ACCOUNTS)
    context: ApplicationContext = get_context(request)
    return [presenters.api_user(item) for item in context.auth.list_users(requester=user)]


@accounts_router.post("", response_model=schemas.ApiUser, status_code=status.HTTP_201_CREATED)
def create_account(
    request: Request, user: CurrentUser, body: schemas.AccountCreate
) -> schemas.ApiUser:
    """Create one account. Administrators only."""
    require_permission(user, Permission.MANAGE_ACCOUNTS)
    context: ApplicationContext = get_context(request)
    try:
        created = context.auth.create_user(
            username=body.username,
            display_name=body.display_name,
            role=normalise_role(body.role),
            password=body.password,
            requester=user,
        )
    except (SecurityError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.api_user(created)


@accounts_router.patch("/{username}/role", response_model=schemas.ApiUser)
def set_account_role(
    request: Request, user: CurrentUser, username: str, body: RoleChange
) -> schemas.ApiUser:
    """Change an account's role. Administrators only."""
    require_permission(user, Permission.MANAGE_ACCOUNTS)
    context: ApplicationContext = get_context(request)
    try:
        updated = context.auth.set_role(
            username=username, role=normalise_role(body.role), requester=user
        )
    except (SecurityError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    get_token_store(request).refresh_user(updated)
    return presenters.api_user(updated)


@accounts_router.patch("/{username}/active", response_model=schemas.ApiUser)
def set_account_active(
    request: Request, user: CurrentUser, username: str, body: ActiveChange
) -> schemas.ApiUser:
    """Enable or disable an account. Administrators only."""
    require_permission(user, Permission.MANAGE_ACCOUNTS)
    context: ApplicationContext = get_context(request)
    try:
        updated = context.auth.set_active(username=username, active=body.active, requester=user)
    except SecurityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    get_token_store(request).refresh_user(updated)
    return presenters.api_user(updated)


@accounts_router.post("/{username}/password")
def change_password(
    request: Request, user: CurrentUser, username: str, body: PasswordChange
) -> dict[str, bool]:
    """Change a password.

    Operators changing their own password must prove the current one; an
    administrator resetting someone else's need not (and never learns it).
    Every token for the account is revoked, so a self-service change means
    logging in again.
    """
    context: ApplicationContext = get_context(request)
    is_self = username.strip().lower() == user.username.lower()
    if not is_self:
        require_permission(user, Permission.MANAGE_ACCOUNTS)
    try:
        context.auth.change_password(
            username=username,
            new_password=body.new_password,
            requester=user,
            current_password=body.current_password,
        )
    except SecurityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    get_token_store(request).revoke_all_for(username)
    return {"changed": True}
