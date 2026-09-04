"""Redaction of sensitive values.

``SECURITY.md`` forbids logging, printing, exporting or committing PINs,
passwords, card identifiers, biometric templates and application secrets.
Everything that formats data for humans (logs, diagnostics, GUI labels) must
route through this module.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Final

__all__ = [
    "REDACTED",
    "SENSITIVE_KEY_PARTS",
    "is_sensitive_key",
    "mask_secret",
    "redact_bytes",
    "redact_mapping",
    "redact_text",
]

REDACTED: Final = "***REDACTED***"

#: Substrings that mark a mapping key as holding sensitive data.
SENSITIVE_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "pin",
        "password",
        "passwd",
        "secret",
        "token",
        "card",
        "credential",
        "template",
        "fingerprint",
        "face",
        "biometric",
        "comm_key",
        "commkey",
        "api_key",
        "apikey",
    }
)

_SENSITIVE_TEXT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(" + "|".join(sorted(re.escape(part) for part in SENSITIVE_KEY_PARTS)) + r")"
    r"[\"']?\s*[:=]\s*[\"']?(?P<value>[^\s,;}\"']+)"
)


def is_sensitive_key(key: str) -> bool:
    """Return ``True`` when ``key`` names a value that must never be logged."""
    normalised = key.strip().lower().replace("-", "_")
    return any(part in normalised for part in SENSITIVE_KEY_PARTS)


def mask_secret(value: str | None, *, keep: int = 0) -> str:
    """Mask ``value``, optionally keeping the final ``keep`` characters.

    ``keep`` exists for GUI affordances such as showing that a PIN is set. It
    never keeps more than a third of the value and never keeps anything from a
    value shorter than four characters.
    """
    if value is None:
        return ""
    if not value:
        return ""
    if keep <= 0 or len(value) < 4:
        return "*" * len(value)
    keep = min(keep, len(value) // 3)
    if keep <= 0:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


def redact_bytes(data: bytes) -> str:
    """Describe a byte buffer without disclosing its contents."""
    return f"<{len(data)} bytes redacted>"


def redact_text(text: str) -> str:
    """Redact ``key=value`` / ``key: value`` pairs whose key looks sensitive."""

    def _replace(match: re.Match[str]) -> str:
        return match.group(0).replace(match.group("value"), REDACTED)

    return _SENSITIVE_TEXT_PATTERN.sub(_replace, text)


def redact_mapping(mapping: Mapping[str, Any], *, _depth: int = 0) -> dict[str, Any]:
    """Return a copy of ``mapping`` with sensitive values replaced.

    Nested mappings and sequences are redacted recursively. Recursion is bounded
    so that a malformed or cyclic structure cannot stall logging.
    """
    if _depth >= 8:
        return {"...": "<max redaction depth reached>"}

    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        if is_sensitive_key(key):
            redacted[key] = REDACTED
        else:
            redacted[key] = _redact_value(value, _depth=_depth + 1)
    return redacted


def _redact_value(value: Any, *, _depth: int) -> Any:
    if isinstance(value, Mapping):
        return redact_mapping(value, _depth=_depth)
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes | bytearray):
        return redact_bytes(bytes(value))
    if isinstance(value, Iterable) and not isinstance(value, str | bytes | bytearray):
        return [_redact_value(item, _depth=_depth + 1) for item in value]
    return value
