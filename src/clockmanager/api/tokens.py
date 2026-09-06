"""Bearer-token session store for the web/API boundary (PHASE 17).

Local accounts are verified through :class:`AuthService` (salted PBKDF2,
constant-time compare); a successful login is exchanged for an opaque
random bearer token so the password does not travel on every request.
Tokens live only in process memory, expire after 24 hours, and are
revoked on logout.

This is deliberately not a JWT: there is no shared secret to manage and
nothing to verify offline. The trade-off is that tokens do not survive a
service restart, which suits a LAN appliance (re-login after a restart)
and is documented in ``STATUS.md``.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from clockmanager.services.auth import AuthenticatedUser

__all__ = ["TOKEN_TTL", "TokenStore"]

#: How long an issued token stays valid.
TOKEN_TTL: timedelta = timedelta(hours=24)


@dataclass(slots=True)
class _Entry:
    user: AuthenticatedUser
    expires_at: datetime


class TokenStore:
    """In-memory bearer-token sessions. Thread-safe."""

    def __init__(self, *, ttl: timedelta = TOKEN_TTL) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._tokens: dict[str, _Entry] = {}

    def issue(self, user: AuthenticatedUser) -> tuple[str, datetime]:
        """Create a token for ``user`` and return it with its expiry."""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + self._ttl
        with self._lock:
            self._tokens[token] = _Entry(user=user, expires_at=expires_at)
        return token, expires_at

    def resolve(self, token: str) -> AuthenticatedUser | None:
        """Return the user for ``token``, or ``None`` when unknown/expired."""
        now = datetime.now(UTC)
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None:
                return None
            if entry.expires_at <= now:
                del self._tokens[token]
                return None
            return entry.user

    def revoke(self, token: str) -> None:
        """Forget one token. Never raises for an unknown token."""
        with self._lock:
            self._tokens.pop(token, None)

    def revoke_all_for(self, username: str) -> int:
        """Forget every token issued to ``username``; return how many."""
        lowered = username.strip().lower()
        with self._lock:
            doomed = [
                token
                for token, entry in self._tokens.items()
                if entry.user.username.lower() == lowered
            ]
            for token in doomed:
                del self._tokens[token]
        return len(doomed)

    def refresh_user(self, user: AuthenticatedUser) -> None:
        """Replace the stored identity for ``user`` (role/active changes)."""
        with self._lock:
            for token, entry in self._tokens.items():
                if entry.user.username.lower() == user.username.lower():
                    self._tokens[token] = _Entry(user=user, expires_at=entry.expires_at)
