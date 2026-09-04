"""Application-wide exception hierarchy.

Every layer raises explicit, typed exceptions. Later phases extend this
hierarchy (for example the protocol layer in PHASE 01) rather than raising
bare built-ins.
"""

from __future__ import annotations

__all__ = [
    "ClockManagerError",
    "ConfigurationError",
    "DeviceError",
    "PersistenceError",
    "SecurityError",
]


class ClockManagerError(Exception):
    """Base class for all NGTeco Clock Manager errors."""


class ConfigurationError(ClockManagerError):
    """Raised when application configuration is missing or invalid."""


class PersistenceError(ClockManagerError):
    """Raised when the database cannot be opened, migrated or queried."""


class DeviceError(ClockManagerError):
    """Base class for attendance-device failures.

    The protocol layer (PHASE 01) subclasses this for transport, timeout and
    parsing failures.
    """


class SecurityError(ClockManagerError):
    """Raised when an operation would violate a security rule."""
