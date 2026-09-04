"""MB1 record building.

The counterpart to :mod:`clockmanager.protocol.records`. Pure functions with no
I/O, so every byte of an outgoing packet is unit-testable before any device
sees it.

Why this module exists at all
----------------------------

``pyzk.set_user()`` builds ``pack("HB8s24s4sx7sx24s", ...)`` -- a **72-byte**
packet. The NG-MB1 user record is **120 bytes** with a completely different
field layout (``PROTOCOL.md``). The two are not compatible, so the generic
writer is never called; this module builds the verified 120-byte record
instead, and :func:`build_user_record` refuses to emit anything that is not
exactly that size.

The credential region
---------------------

Bytes 3:35 hold credential data whose internal layout is **not known**. This
module therefore treats it as opaque: on an update it is copied byte-for-byte
from the device's own record, so a write that only changes a name cannot
silently destroy a user's PIN. Setting a credential requires an unverified
guess at the layout and is gated behind
:data:`~clockmanager.protocol.capabilities.Capability.WRITE_USER_PASSWORD`.

Credential bytes never leave this layer: :class:`RawUserRecord` exists so the
adapter can read-modify-write without the credential region reaching the
domain, service or GUI layers (``SECURITY.md``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from struct import pack

from clockmanager.domain.users import CredentialAction
from clockmanager.protocol.constants import (
    MAX_USER_UID,
    MB1_USER_RECORD_SIZE,
    USER_CREDENTIAL_SIZE,
    USER_CREDENTIAL_SLICE,
    USER_FIRST_NAME_SIZE,
    USER_FIRST_NAME_SLICE,
    USER_ID_SIZE,
    USER_ID_SLICE,
    USER_LAST_NAME_SIZE,
    USER_LAST_NAME_SLICE,
    USER_PASSWORD_CANDIDATE_SLICE,
    USER_PRIVILEGE_OFFSET,
    USER_UID_SLICE,
    WRITABLE_PRIVILEGES,
)
from clockmanager.protocol.errors import DeviceParseError, DeviceValidationError

__all__ = [
    "RawUserRecord",
    "build_user_record",
    "describe_record_fields",
    "encode_fixed_text",
    "parse_raw_user_records",
]


@dataclass(frozen=True, slots=True)
class RawUserRecord:
    """One 120-byte device record, kept whole so a write can preserve it.

    This is a protocol-internal type. It holds the credential region, so it
    must never be returned from :mod:`clockmanager.services` or reach the GUI.
    ``raw`` is excluded from ``repr`` for the same reason a communication
    password is (``SECURITY.md``): a traceback or a log line must not be able
    to disclose it.
    """

    uid: int
    user_id: str
    raw: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.raw) != MB1_USER_RECORD_SIZE:
            raise DeviceParseError(
                f"A raw MB1 record must be {MB1_USER_RECORD_SIZE} bytes, got {len(self.raw)}."
            )

    @property
    def credential_region(self) -> bytes:
        """The opaque 32-byte credential region. Never decode or log this."""
        return self.raw[USER_CREDENTIAL_SLICE]

    @property
    def has_credential_data(self) -> bool:
        return any(self.credential_region)


def encode_fixed_text(value: str, size: int, *, what: str, encoding: str = "utf-8") -> bytes:
    """Encode ``value`` NUL-padded to exactly ``size`` bytes.

    Raises rather than truncating: silently shortening a name or a user ID
    would write a record that does not match what the operator confirmed.
    """
    raw = value.encode(encoding, errors="strict")
    if len(raw) > size:
        raise DeviceValidationError(
            f"{what} is {len(raw)} bytes encoded, which does not fit the device's "
            f"{size}-byte field. Shorten it to fit."
        )
    if b"\x00" in raw:
        raise DeviceValidationError(f"{what} must not contain a NUL character.")
    return raw.ljust(size, b"\x00")


def _build_credential_region(
    action: CredentialAction,
    *,
    existing: bytes | None,
    password: str | None,
    encoding: str,
) -> bytes:
    """Produce the 32-byte credential region for an outgoing record."""
    if action is CredentialAction.PRESERVE:
        if existing is None:
            # A new user has no existing region to preserve; an empty region is
            # the only thing that can be written without guessing.
            return bytes(USER_CREDENTIAL_SIZE)
        if len(existing) != USER_CREDENTIAL_SIZE:  # pragma: no cover - guarded by RawUserRecord
            raise DeviceValidationError(
                f"Credential region must be {USER_CREDENTIAL_SIZE} bytes, got {len(existing)}."
            )
        return existing

    if action is CredentialAction.CLEAR:
        return bytes(USER_CREDENTIAL_SIZE)

    if password is None or not password:
        raise DeviceValidationError(
            "Setting a credential requires a value. Use CredentialAction.CLEAR to remove one."
        )

    # UNVERIFIED layout. Everything outside the candidate password field keeps
    # whatever the device had, so a wrong guess damages as little as possible.
    region = bytearray(existing if existing is not None else bytes(USER_CREDENTIAL_SIZE))
    field_start = USER_PASSWORD_CANDIDATE_SLICE.start - USER_CREDENTIAL_SLICE.start
    field_size = USER_PASSWORD_CANDIDATE_SLICE.stop - USER_PASSWORD_CANDIDATE_SLICE.start
    encoded = encode_fixed_text(password, field_size, what="PIN or password", encoding=encoding)
    region[field_start : field_start + field_size] = encoded
    return bytes(region)


def build_user_record(
    *,
    uid: int,
    user_id: str,
    first_name: str = "",
    last_name: str = "",
    privilege: int,
    credential_action: CredentialAction = CredentialAction.PRESERVE,
    existing: RawUserRecord | None = None,
    password: str | None = None,
    encoding: str = "utf-8",
) -> bytes:
    """Build one exact 120-byte NG-MB1 user record.

    Every field is validated before packing, and the result is length-checked,
    so an incorrectly shaped packet can never reach a device.

    ``existing`` supplies the record currently on the device. It is required
    for :data:`CredentialAction.PRESERVE` on an update and is what makes a
    name-only change non-destructive to the user's credential.
    """
    if not 0 <= uid <= MAX_USER_UID:
        raise DeviceValidationError(
            f"UID must be between 0 and {MAX_USER_UID}; the device stores it in two bytes."
        )
    if privilege not in WRITABLE_PRIVILEGES:
        raise DeviceValidationError(
            f"Refusing to write privilege {privilege}. Only the values verified on the "
            f"real device may be written: {list(WRITABLE_PRIVILEGES)} "
            "(0 Employee, 14 Admin)."
        )
    if not user_id.strip():
        raise DeviceValidationError("User ID must not be empty.")

    record = (
        pack("<H", uid)
        + bytes([privilege])
        + _build_credential_region(
            credential_action,
            existing=None if existing is None else existing.credential_region,
            password=password,
            encoding=encoding,
        )
        + encode_fixed_text(first_name, USER_FIRST_NAME_SIZE, what="First name", encoding=encoding)
        + encode_fixed_text(last_name, USER_LAST_NAME_SIZE, what="Last name", encoding=encoding)
        + encode_fixed_text(user_id, USER_ID_SIZE, what="User ID", encoding=encoding)
    )

    if len(record) != MB1_USER_RECORD_SIZE:  # pragma: no cover - construction guard
        raise DeviceValidationError(
            f"Built a {len(record)}-byte record, expected exactly {MB1_USER_RECORD_SIZE}. "
            "Refusing to send it."
        )
    return record


def parse_raw_user_records(payload: bytes) -> list[RawUserRecord]:
    """Split a buffered user read into whole records, credential region intact.

    Used only by the read-modify-write path. Ordinary reads go through
    :func:`clockmanager.protocol.records.parse_user_payload`, which discards
    the credential region entirely.
    """
    # Imported here: records.py is the parsing module and importing it at
    # module scope would make the two modules mutually dependent.
    from clockmanager.protocol.records import (
        parse_user_record,
        split_size_prefixed_payload,
    )

    if not payload:
        return []

    declared_size, body = split_size_prefixed_payload(payload, what="User data")
    if not body:
        return []
    if 0 < declared_size <= len(body):
        body = body[:declared_size]

    if len(body) % MB1_USER_RECORD_SIZE != 0:
        raise DeviceParseError(
            f"User data is {len(body)} bytes, which is not a multiple of the verified "
            f"MB1 record size of {MB1_USER_RECORD_SIZE}. Refusing to guess at an "
            "unrecognised layout."
        )

    records: list[RawUserRecord] = []
    for offset in range(0, len(body), MB1_USER_RECORD_SIZE):
        chunk = body[offset : offset + MB1_USER_RECORD_SIZE]
        parsed = parse_user_record(chunk)
        records.append(RawUserRecord(uid=parsed.device_uid, user_id=parsed.user_id, raw=chunk))
    return records


def describe_record_fields(record: bytes) -> dict[str, str]:
    """Describe an outgoing record for an audit entry, without its credential.

    The credential region is reported as a presence flag only, never as bytes.
    """
    if len(record) != MB1_USER_RECORD_SIZE:
        raise DeviceValidationError(
            f"Cannot describe a {len(record)}-byte record; expected {MB1_USER_RECORD_SIZE}."
        )

    def _text(raw: bytes) -> str:
        return raw.split(b"\x00")[0].decode("utf-8", errors="replace").strip()

    return {
        "uid": str(int.from_bytes(record[USER_UID_SLICE], "little")),
        "privilege": str(record[USER_PRIVILEGE_OFFSET]),
        "first_name": _text(record[USER_FIRST_NAME_SLICE]),
        "last_name": _text(record[USER_LAST_NAME_SLICE]),
        "user_id": _text(record[USER_ID_SLICE]),
        "credential_present": str(any(record[USER_CREDENTIAL_SLICE])),
    }
