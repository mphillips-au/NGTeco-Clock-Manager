"""Local-account password hashing (PHASE 07).

``SECURITY.md`` forbids persisting a PIN, card identifier or biometric
template. Application login passwords are different: they must be stored to
be verified. They are stored only as salted PBKDF2-HMAC-SHA256 hashes from
the standard library — no new dependency — and plaintext passwords exist
only for the duration of one hash or verify call.

Nothing in this module logs. Callers must not log passwords either; the
``password`` redaction rule in :mod:`clockmanager.security.redaction`
is the backstop, not the plan.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

__all__ = [
    "PASSWORD_HASH_PREFIX",
    "hash_password",
    "validate_password",
    "validate_username",
    "verify_password",
]

#: Identifies hashes produced by this module. Stored rows carry the full
#: ``prefix$iterations$salt_hex$hash_hex`` string, so parameters can evolve
#: without invalidating existing accounts.
PASSWORD_HASH_PREFIX = "pbkdf2-sha256"

_ITERATIONS = 210_000
_SALT_BYTES = 16
_MIN_PASSWORD_LENGTH = 8
_MAX_PASSWORD_LENGTH = 128
_MIN_USERNAME_LENGTH = 3
_MAX_USERNAME_LENGTH = 32
_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]+")

_MIN_ITERATIONS_ACCEPTED = 100_000


def validate_username(username: str) -> list[str]:
    """Return human-readable problems with ``username``, or ``[]``."""
    cleaned = username.strip()
    problems: list[str] = []
    if not cleaned:
        problems.append("Username must not be empty.")
        return problems
    if not _MIN_USERNAME_LENGTH <= len(cleaned) <= _MAX_USERNAME_LENGTH:
        problems.append(
            f"Username must be {_MIN_USERNAME_LENGTH} to {_MAX_USERNAME_LENGTH} characters."
        )
    if _USERNAME_PATTERN.fullmatch(cleaned) is None:
        problems.append("Username may only contain letters, digits, '.', '_' and '-'.")
    return problems


def validate_password(password: str) -> list[str]:
    """Return human-readable problems with ``password``, or ``[]``.

    Only length is enforced. Complexity rules add support burden without
    measurable benefit for a local desktop application; length is what
    matters for a PBKDF2 hash.
    """
    problems: list[str] = []
    if not password:
        problems.append("Password must not be empty.")
        return problems
    if len(password) < _MIN_PASSWORD_LENGTH:
        problems.append(f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.")
    if len(password) > _MAX_PASSWORD_LENGTH:
        problems.append(f"Password must be at most {_MAX_PASSWORD_LENGTH} characters.")
    return problems


def hash_password(password: str) -> str:
    """Hash ``password`` with a fresh random salt.

    Raises :class:`ValueError` when the password fails validation, so an
    unusable account can never be created by accident.
    """
    problems = validate_password(password)
    if problems:
        raise ValueError(" ".join(problems))
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{PASSWORD_HASH_PREFIX}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Return whether ``password`` matches the stored hash.

    Unknown or malformed hashes return ``False`` rather than raising, so a
    corrupt row fails closed. Comparison is constant-time.
    """
    try:
        prefix, iterations_text, salt_hex, hash_hex = stored.split("$")
        if prefix != PASSWORD_HASH_PREFIX:
            return False
        iterations = int(iterations_text)
        if iterations < _MIN_ITERATIONS_ACCEPTED:
            return False
        expected = bytes.fromhex(hash_hex)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)
