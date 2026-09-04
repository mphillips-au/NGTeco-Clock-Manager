"""NG-MB1 protocol constants.

Every value here is either a ZKTeco protocol constant confirmed against the
``pyzk`` reference implementation, or an MB1 record layout offset verified
against the real device (``PROTOCOL.md``).
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "ADMIN_PRIVILEGE",
    "ATTENDANCE_RECORD_SIZES",
    "CMD_ATTLOG_RRQ",
    "CMD_REG_EVENT",
    "CMD_USERTEMP_RRQ",
    "DEFAULT_PORT",
    "EF_ATTLOG",
    "EMPLOYEE_PRIVILEGE",
    "FCT_USER",
    "LIVE_EVENT_BUFFER_BYTES",
    "MB1_USER_RECORD_SIZE",
    "SIZE_PREFIX_BYTES",
    "USER_CREDENTIAL_SLICE",
    "USER_FIRST_NAME_SLICE",
    "USER_ID_SLICE",
    "USER_LAST_NAME_SLICE",
    "USER_PRIVILEGE_OFFSET",
    "USER_UID_SLICE",
]

#: TCP port used by the NG-MB1 (verified on the real device).
DEFAULT_PORT: Final = 4370

# -- ZKTeco commands used by the read-only core -------------------------------

#: Read user data (ZK ``CMD_USERTEMP_RRQ``).
CMD_USERTEMP_RRQ: Final = 9
#: Read all attendance records (ZK ``CMD_ATTLOG_RRQ``).
CMD_ATTLOG_RRQ: Final = 13
#: Buffered-read function selector for user data (ZK ``FCT_USER``).
FCT_USER: Final = 5
#: Register for real-time events (ZK ``CMD_REG_EVENT``).
CMD_REG_EVENT: Final = 500
#: Real-time event flag for attendance logs (ZK ``EF_ATTLOG``).
EF_ATTLOG: Final = 1
#: Largest live-event datagram the device sends.
LIVE_EVENT_BUFFER_BYTES: Final = 1032

# -- MB1 120-byte user record -------------------------------------------------

#: The NG-MB1 returns exactly 120 bytes per user, NOT the generic ZKTeco
#: 28- or 72-byte shapes. Verified against multiple real users.
MB1_USER_RECORD_SIZE: Final = 120

#: Buffered reads are prefixed with a 4-byte little-endian total size.
SIZE_PREFIX_BYTES: Final = 4

USER_UID_SLICE: Final = slice(0, 2)
USER_PRIVILEGE_OFFSET: Final = 2
#: Credential/PIN region. Never parsed, never returned, never logged.
USER_CREDENTIAL_SLICE: Final = slice(3, 35)
USER_FIRST_NAME_SLICE: Final = slice(35, 59)
USER_LAST_NAME_SLICE: Final = slice(59, 96)
USER_ID_SLICE: Final = slice(96, 120)

#: Verified privilege byte values.
EMPLOYEE_PRIVILEGE: Final = 0
ADMIN_PRIVILEGE: Final = 14

# -- Attendance ---------------------------------------------------------------

#: Attendance record sizes defined by the ZKTeco protocol. Which one an MB1
#: uses is determined at runtime from the payload, never assumed.
ATTENDANCE_RECORD_SIZES: Final[tuple[int, ...]] = (8, 16, 40)
