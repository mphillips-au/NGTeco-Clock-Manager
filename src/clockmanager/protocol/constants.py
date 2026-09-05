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
    "CMD_ACK_OK",
    "CMD_ATTLOG_RRQ",
    "CMD_DELETE_USER",
    "CMD_REFRESHDATA",
    "CMD_REG_EVENT",
    "CMD_USERTEMP_RRQ",
    "CMD_USER_WRQ",
    "DEFAULT_PORT",
    "EF_ATTLOG",
    "EMPLOYEE_PRIVILEGE",
    "FCT_USER",
    "LIVE_EVENT_BUFFER_BYTES",
    "MAX_DEVICE_YEAR",
    "MAX_USER_UID",
    "MB1_USER_RECORD_SIZE",
    "MIN_DEVICE_YEAR",
    "PYZK_USER_PACKET_SIZE",
    "SIZE_PREFIX_BYTES",
    "USER_CREDENTIAL_SIZE",
    "USER_CREDENTIAL_SLICE",
    "USER_FIRST_NAME_SIZE",
    "USER_FIRST_NAME_SLICE",
    "USER_ID_SIZE",
    "USER_ID_SLICE",
    "USER_LAST_NAME_SIZE",
    "USER_LAST_NAME_SLICE",
    "USER_PASSWORD_CANDIDATE_SLICE",
    "USER_PRIVILEGE_OFFSET",
    "USER_UID_SLICE",
    "WRITABLE_PRIVILEGES",
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

# -- ZKTeco commands used by the PHASE 03 write path --------------------------

#: Upload one user record, PC to terminal (ZK ``CMD_USER_WRQ``). The payload is
#: an application-built 120-byte MB1 record, never pyzk's 72-byte packet.
CMD_USER_WRQ: Final = 8
#: Delete one user by UID (ZK ``CMD_DELETE_USER``).
CMD_DELETE_USER: Final = 18
#: Ask the device to reload its interior data after a write (ZK
#: ``CMD_REFRESHDATA``). Without it the device may keep serving stale records.
CMD_REFRESHDATA: Final = 1013
#: Successful command acknowledgement (ZK ``CMD_ACK_OK``).
CMD_ACK_OK: Final = 2000

#: The size of the user packet ``pyzk.set_user()`` builds
#: (``pack("HB8s24s4sx7sx24s", ...)``). It is recorded here as the concrete
#: evidence that the generic writer is incompatible with the MB1's 120-byte
#: record, and is never used to build a packet.
PYZK_USER_PACKET_SIZE: Final = 72

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

USER_CREDENTIAL_SIZE: Final = 32
USER_FIRST_NAME_SIZE: Final = 24
USER_LAST_NAME_SIZE: Final = 37
USER_ID_SIZE: Final = 24

#: UNVERIFIED. Where a PIN is believed to sit inside the 32-byte credential
#: region, by analogy with the generic ZKTeco record, whose 8-byte password
#: field follows the privilege byte. The rest of the region has no known
#: meaning and is always preserved byte-for-byte from the device's own record.
#: Nothing writes here unless an operator has explicitly unlocked
#: ``Capability.WRITE_USER_PASSWORD`` for controlled testing.
USER_PASSWORD_CANDIDATE_SLICE: Final = slice(3, 11)

#: Largest UID the 2-byte UID field can hold.
MAX_USER_UID: Final = 0xFFFF

#: Verified privilege byte values.
EMPLOYEE_PRIVILEGE: Final = 0
ADMIN_PRIVILEGE: Final = 14

#: The only privilege values this application will ever write. A record read
#: back with some other value is preserved and reported verbatim, but an
#: unverified privilege is never sent to a device.
WRITABLE_PRIVILEGES: Final[tuple[int, ...]] = (EMPLOYEE_PRIVILEGE, ADMIN_PRIVILEGE)

# -- Attendance ---------------------------------------------------------------

#: Attendance record sizes defined by the ZKTeco protocol. The project NG-MB1
#: was observed using **40** (PHASE 14), but the size is still resolved at
#: runtime from the payload and the device's own record count, never assumed:
#: other firmware in the family may differ.
ATTENDANCE_RECORD_SIZES: Final[tuple[int, ...]] = (8, 16, 40)

#: Plausible range for a device-reported year. Both ZKTeco timestamp encodings
#: count from 2000 and neither has an "invalid" representation, so a corrupt
#: packet decodes to a real-looking date instead of failing. Bounding the year
#: is what stops a garbled punch from being stored and paid.
MIN_DEVICE_YEAR: Final = 2000
MAX_DEVICE_YEAR: Final = 2099
