"""Device/protocol layer for NGTeco attendance clocks.

PHASE 01 provides the READ-ONLY NG-MB1 core. Rules that govern this layer
(``AGENTS.md``, ``PROTOCOL.md``):

* An application-owned MB1 adapter is required. ``pyzk`` is used as a proven
  transport and as a source of command behaviour, never as a drop-in
  replacement for MB1-specific parsing.
* The MB1 user record is 120 bytes, not the generic 28/72-byte ZKTeco shape.
* Unknown protocol behaviour stays unsupported until proven on real hardware;
  see :class:`~clockmanager.protocol.capabilities.DeviceCapabilities`.
* No write, delete, clear or reset operation exists here. Adding one requires a
  verified 120-byte write path, validation, explicit confirmation, read-back
  verification and audit logging.
"""

from __future__ import annotations

from clockmanager.protocol.capabilities import (
    NG_MB1_CAPABILITIES,
    Capability,
    CapabilityState,
    DeviceCapabilities,
    Support,
)
from clockmanager.protocol.errors import (
    DeviceAuthenticationError,
    DeviceCapabilityError,
    DeviceConnectionError,
    DeviceError,
    DeviceNotConnectedError,
    DeviceParseError,
    DeviceProtocolError,
    DeviceTimeoutError,
)
from clockmanager.protocol.interface import AttendanceDevice, DeviceConnectionSettings
from clockmanager.protocol.mb1 import NGTecoMB1Device
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript
from clockmanager.protocol.records import (
    parse_attendance_payload,
    parse_live_event,
    parse_user_payload,
    parse_user_record,
)
from clockmanager.protocol.retry import RetryPolicy

__all__ = [
    "NG_MB1_CAPABILITIES",
    "AttendanceDevice",
    "Capability",
    "CapabilityState",
    "DeviceAuthenticationError",
    "DeviceCapabilities",
    "DeviceCapabilityError",
    "DeviceConnectionError",
    "DeviceConnectionSettings",
    "DeviceError",
    "DeviceNotConnectedError",
    "DeviceParseError",
    "DeviceProtocolError",
    "DeviceTimeoutError",
    "MockAttendanceDevice",
    "MockDeviceScript",
    "NGTecoMB1Device",
    "RetryPolicy",
    "Support",
    "parse_attendance_payload",
    "parse_live_event",
    "parse_user_payload",
    "parse_user_record",
]
