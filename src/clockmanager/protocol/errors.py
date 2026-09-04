"""Structured protocol exceptions.

All inherit from :class:`clockmanager.errors.DeviceError` so callers can catch
device problems as one category, or a specific failure when they can act on it.
"""

from __future__ import annotations

from clockmanager.errors import DeviceError

__all__ = [
    "DeviceAuthenticationError",
    "DeviceCapabilityError",
    "DeviceConnectionError",
    "DeviceError",
    "DeviceNotConnectedError",
    "DeviceParseError",
    "DeviceProtocolError",
    "DeviceTimeoutError",
]


class DeviceConnectionError(DeviceError):
    """The device could not be reached, or the connection was lost."""


class DeviceTimeoutError(DeviceConnectionError):
    """The device did not answer within the configured timeout."""


class DeviceAuthenticationError(DeviceError):
    """The device rejected the communication password."""


class DeviceProtocolError(DeviceError):
    """The device answered, but not in a way the protocol allows."""


class DeviceParseError(DeviceProtocolError):
    """A device payload did not match a verified layout.

    Raised instead of guessing at an unrecognised structure.
    """


class DeviceNotConnectedError(DeviceError):
    """An operation requiring a live connection was attempted while closed."""


class DeviceCapabilityError(DeviceError):
    """An operation was attempted that this device does not support, or whose
    support has not been verified on real hardware."""
