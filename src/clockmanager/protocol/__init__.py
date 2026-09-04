"""Device/protocol layer for NGTeco attendance clocks.

PHASE 01 provides the READ-ONLY NG-MB1 core. Rules that govern this layer
(``AGENTS.md``, ``PROTOCOL.md``):

* An application-owned MB1 adapter is required. ``pyzk`` is used as a proven
  transport and as a source of command behaviour, never as a drop-in
  replacement for MB1-specific parsing.
* The MB1 user record is 120 bytes, not the generic 28/72-byte ZKTeco shape.
* Unknown protocol behaviour stays unsupported until proven on real hardware;
  see :class:`~clockmanager.protocol.capabilities.DeviceCapabilities`.
* PHASE 03 adds a user write path built on an application-owned 120-byte
  record. It is gated on an operator unlock, performs read-back verification
  inside the adapter, and never calls ``pyzk.set_user()``.
* No attendance-clearing, factory-reset or biometric-write operation exists
  here, and none may be added without device evidence.
"""

from __future__ import annotations

from clockmanager.protocol.builders import (
    RawUserRecord,
    build_user_record,
    describe_record_fields,
    parse_raw_user_records,
)
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
    DeviceValidationError,
    DeviceVerificationError,
    DeviceWriteError,
)
from clockmanager.protocol.interface import (
    AttendanceDevice,
    DeviceConnectionSettings,
    WritableUserDevice,
)
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
    "DeviceValidationError",
    "DeviceVerificationError",
    "DeviceWriteError",
    "MockAttendanceDevice",
    "MockDeviceScript",
    "NGTecoMB1Device",
    "RawUserRecord",
    "RetryPolicy",
    "Support",
    "WritableUserDevice",
    "build_user_record",
    "describe_record_fields",
    "parse_attendance_payload",
    "parse_live_event",
    "parse_raw_user_records",
    "parse_user_payload",
    "parse_user_record",
]
