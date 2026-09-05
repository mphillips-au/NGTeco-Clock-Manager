"""NGTeco NG-MB1 device adapter.

``pyzk`` supplies the transport, command encoding and connection handling that
were verified working against the real MB1. Everything the MB1 does
*differently* is owned by this application (``AGENTS.md``):

* User records are 120 bytes. ``pyzk.get_users()`` only understands the generic
  28/72-byte ZKTeco shapes and silently mis-parses MB1 data, so this adapter
  reads the raw buffer and applies :func:`parse_user_payload` instead.
* ``pyzk.get_attendance()`` calls its own ``get_users()`` internally to map UIDs
  to user IDs, which inherits that mis-parse, so attendance is parsed here too.
* ``pyzk.live_capture()`` has the same internal dependency and additionally does
  ``int(user_id)`` for any user it cannot find, which raises on a non-numeric
  ID. The live loop is therefore driven directly from the socket here.

PHASE 03 adds a user write path. It is **not** a call to ``pyzk.set_user()``:
that builds a 72-byte packet for a device whose records are 120 bytes. This
adapter builds the exact 120-byte record itself
(:mod:`clockmanager.protocol.builders`) and sends it with ``CMD_USER_WRQ``.

Every write follows the same sequence, in the adapter so it cannot be skipped:
read the device, locate the target record, build, send, verify the device
acknowledged it, read back, and compare. Only then does the caller get an
outcome to audit. Writes are additionally refused unless an operator has
explicitly unlocked the capability, because no MB1 has yet accepted a record
from this path.

Attendance clearing, factory reset and biometric writing do not exist here and
must not be added.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from struct import pack, unpack
from types import TracebackType
from typing import Any, Self

from zk import ZK
from zk.exception import ZKErrorConnection, ZKErrorResponse, ZKNetworkError

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.models import (
    AttendanceEvent,
    DeviceIdentity,
    DeviceInfo,
    DeviceOption,
    DeviceStorage,
    DeviceUser,
    FingerprintSlot,
    OperationLogEntry,
    StorageCounter,
)
from clockmanager.domain.users import (
    UserDraft,
    UserWriteOutcome,
    describe_changes,
)
from clockmanager.protocol.builders import (
    RawUserRecord,
    build_user_record,
    parse_raw_user_records,
)
from clockmanager.protocol.capabilities import (
    NG_MB1_CAPABILITIES,
    WRITE_CAPABILITIES,
    Capability,
    DeviceCapabilities,
)
from clockmanager.protocol.constants import (
    CMD_ATTLOG_RRQ,
    CMD_DB_RRQ,
    CMD_DELETE_USER,
    CMD_OPTIONS_RRQ,
    CMD_REFRESHDATA,
    CMD_REG_EVENT,
    CMD_USER_WRQ,
    CMD_USERTEMP_RRQ,
    EF_ATTLOG,
    FCT_FINGERTMP,
    FCT_OPLOG,
    FCT_USER,
    LIVE_EVENT_BUFFER_BYTES,
    MAX_USER_UID,
    MB1_USER_RECORD_SIZE,
    OPTION_RESPONSE_BYTES,
    USER_CREDENTIAL_SLICE,
)
from clockmanager.protocol.errors import (
    DeviceConnectionError,
    DeviceNotConnectedError,
    DeviceProtocolError,
    DeviceTimeoutError,
    DeviceValidationError,
    DeviceVerificationError,
    DeviceWriteError,
)
from clockmanager.protocol.interface import DeviceConnectionSettings
from clockmanager.protocol.options import (
    DeviceOptionSpec,
    is_sensitive_option_name,
    option_specs,
)
from clockmanager.protocol.records import (
    parse_attendance_payload,
    parse_device_option_response,
    parse_fingerprint_payload,
    parse_live_event,
    parse_operation_log_payload,
    parse_user_payload,
    parse_user_record,
)
from clockmanager.protocol.retry import RetryPolicy, call_with_retry

__all__ = ["NGTecoMB1Device", "TransportFactory", "default_transport"]

_logger = get_logger(__name__)

#: Builds the underlying transport. Injectable so tests never need a socket.
TransportFactory = Callable[[DeviceConnectionSettings], Any]

MODEL_NAME = "NG-MB1"

#: Why an operator-unlocked capability is usable. Recorded on the capability
#: itself so it travels into diagnostics and the GUI.
_WRITE_UNLOCK_REASON = (
    "Deliberately enabled by an operator for this installation. The 120-byte "
    "write path is proven on real hardware (PHASE 15); enabling it is still a "
    "decision, because a write changes who can enter the building."
)

#: Why a proven write capability is nonetheless refused. Every device is built
#: with writing locked; an installation opts in.
_WRITE_LOCK_REASON = (
    "This installation has not enabled device writing "
    "(CLOCKMANAGER_ENABLE_DEVICE_WRITES / CLOCKMANAGER_ENABLE_CREDENTIAL_WRITES)."
)


def default_transport(settings: DeviceConnectionSettings) -> ZK:
    """Build the stock pyzk transport.

    Public so diagnostics can wrap it with packet capture
    (:mod:`clockmanager.protocol.trace`); the adapter itself uses it when no
    factory is given.
    """
    return ZK(
        settings.host,
        port=settings.port,
        timeout=int(settings.timeout_seconds),
        password=settings.communication_password,
        force_udp=settings.force_udp,
        ommit_ping=settings.omit_ping,
        encoding=settings.encoding,
        verbose=False,
    )


def _private(transport: Any, name: str) -> Any:
    """Access a name-mangled ``ZK`` attribute, failing loudly if pyzk changes.

    The live-capture loop needs pyzk's socket and acknowledgement helper. This
    keeps that dependency in one place with a clear error instead of an
    ``AttributeError`` deep inside a generator.
    """
    mangled = f"_ZK__{name}"
    try:
        return getattr(transport, mangled)
    except AttributeError as exc:
        raise DeviceProtocolError(
            f"The installed pyzk version does not expose {mangled!r}, which the "
            "MB1 live-capture loop depends on. Pin pyzk or update the adapter."
        ) from exc


class NGTecoMB1Device:
    """Read-only adapter for the NGTeco NG-MB1.

    Implements :class:`clockmanager.protocol.interface.AttendanceDevice`.
    """

    def __init__(
        self,
        settings: DeviceConnectionSettings,
        *,
        transport_factory: TransportFactory = default_transport,
        retry_policy: RetryPolicy | None = None,
        auto_reconnect: bool = True,
        allow_writes: bool = False,
        allow_credential_writes: bool = False,
    ) -> None:
        self._settings = settings
        self._transport_factory = transport_factory
        self._retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self._auto_reconnect = auto_reconnect
        self._transport: Any | None = None
        self._stop_live = False
        self._capabilities = _resolve_capabilities(
            allow_writes=allow_writes,
            allow_credential_writes=allow_credential_writes,
        )

    # -- identity -------------------------------------------------------------

    @property
    def settings(self) -> DeviceConnectionSettings:
        return self._settings

    @property
    def capabilities(self) -> DeviceCapabilities:
        return self._capabilities

    @property
    def is_connected(self) -> bool:
        return self._transport is not None

    # -- connection -----------------------------------------------------------

    def connect(self) -> DeviceInfo:
        """Open the connection and read the device snapshot."""
        self.capabilities.require(Capability.CONNECT)
        self._open()
        _logger.info(
            "Connected to device",
            extra={"device": self._settings.name, "endpoint": self._settings.endpoint},
        )
        return self.get_device_info()

    def _open(self) -> None:
        if self._transport is not None:
            return
        transport = self._transport_factory(self._settings)
        try:
            transport.connect()
        except (ZKErrorConnection, ZKNetworkError, ZKErrorResponse) as exc:
            raise DeviceConnectionError(
                f"Could not connect to {self._settings.endpoint}: {exc}"
            ) from exc
        except OSError as exc:
            raise DeviceConnectionError(
                f"Could not reach {self._settings.endpoint}: {exc}"
            ) from exc
        self._transport = transport

    def _reconnect(self) -> None:
        """Drop and rebuild the transport, used between retry attempts."""
        self.disconnect()
        self._open()

    def disconnect(self) -> None:
        """Close the connection. Safe to call when already closed."""
        transport, self._transport = self._transport, None
        if transport is None:
            return
        try:
            transport.disconnect()
        except (ZKErrorConnection, ZKNetworkError, ZKErrorResponse, OSError) as exc:
            # A failure while closing is not actionable; record and move on.
            _logger.warning(
                "Error while disconnecting",
                extra={"device": self._settings.name, "error": str(exc)},
            )

    def _require_transport(self) -> Any:
        if self._transport is None:
            raise DeviceNotConnectedError(
                f"Device {self._settings.name!r} is not connected. Call connect() first."
            )
        return self._transport

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.disconnect()

    # -- command plumbing -----------------------------------------------------

    def _call(self, operation: Callable[[Any], Any], *, description: str) -> Any:
        """Run a transport call with retry/reconnect and error translation."""

        def _run() -> Any:
            transport = self._require_transport()
            try:
                return operation(transport)
            except TimeoutError as exc:
                raise DeviceTimeoutError(
                    f"{description} timed out after {self._settings.timeout_seconds}s"
                ) from exc
            except (ZKErrorConnection, ZKNetworkError) as exc:
                raise DeviceConnectionError(f"{description} lost the connection: {exc}") from exc
            except ZKErrorResponse as exc:
                raise DeviceProtocolError(
                    f"{description} was rejected by the device: {exc}"
                ) from exc
            except OSError as exc:
                raise DeviceConnectionError(f"{description} failed: {exc}") from exc

        return call_with_retry(
            _run,
            policy=self._retry_policy,
            description=description,
            on_retry=self._reconnect if self._auto_reconnect else None,
        )

    # -- reads ----------------------------------------------------------------

    def get_device_info(self) -> DeviceInfo:
        """Read identity, clock and record counts."""
        self.capabilities.require(Capability.DEVICE_INFO)

        def _read(transport: Any) -> DeviceInfo:
            device_name = _as_text(transport.get_device_name()) or self._settings.name
            identity = DeviceIdentity(
                name=device_name,
                serial_number=_as_text(transport.get_serialnumber()) or None,
                model=MODEL_NAME,
                platform=_as_text(transport.get_platform()) or None,
                firmware_version=_as_text(transport.get_firmware_version()) or None,
            )

            device_time: datetime | None = None
            try:
                device_time = transport.get_time()
            except (ZKErrorResponse, ValueError):
                # The clock is informational; a device that will not report it
                # is still usable for reads.
                _logger.warning("Device did not report its clock", extra={"device": device_name})

            transport.read_sizes()
            return DeviceInfo(
                identity=identity,
                device_time=device_time,
                user_count=_as_count(getattr(transport, "users", None)),
                attendance_count=_as_count(getattr(transport, "records", None)),
                fingerprint_count=_as_count(getattr(transport, "fingers", None)),
                face_count=_as_count(getattr(transport, "faces", None)),
                storage=_storage_from_transport(transport),
            )

        info: DeviceInfo = self._call(_read, description="Reading device info")
        return info

    def get_device_time(self) -> datetime:
        """Read the device clock (naive device-local time)."""
        self.capabilities.require(Capability.READ_TIME)
        result: datetime = self._call(
            lambda transport: transport.get_time(), description="Reading device time"
        )
        return result

    def get_users(self) -> list[DeviceUser]:
        """Read all users using the verified 120-byte MB1 layout."""
        self.capabilities.require(Capability.READ_USERS)

        def _read(transport: Any) -> list[DeviceUser]:
            payload, _size = transport.read_with_buffer(CMD_USERTEMP_RRQ, FCT_USER)
            return parse_user_payload(payload, encoding=self._settings.encoding)

        users: list[DeviceUser] = self._call(_read, description="Reading users")
        _logger.info(
            "Read users from device",
            extra={"device": self._settings.name, "user_count": len(users)},
        )
        return users

    def get_attendance(self) -> list[AttendanceEvent]:
        """Read all stored attendance records."""
        self.capabilities.require(Capability.READ_ATTENDANCE)
        users = self.get_users()

        def _read(transport: Any) -> list[AttendanceEvent]:
            transport.read_sizes()
            record_count = _as_count(getattr(transport, "records", None)) or 0
            payload, _size = transport.read_with_buffer(CMD_ATTLOG_RRQ)
            return parse_attendance_payload(
                payload,
                record_count=record_count,
                users=users,
                encoding=self._settings.encoding,
            )

        events: list[AttendanceEvent] = self._call(_read, description="Reading attendance")
        _logger.info(
            "Read attendance from device",
            extra={"device": self._settings.name, "event_count": len(events)},
        )
        return events

    def read_fingerprint_slots(self) -> list[FingerprintSlot]:
        """Enumerate enrolled fingerprints without reading any template.

        Verified on the project NG-MB1 (PHASE 15): a buffered
        ``CMD_DB_RRQ``/``FCT_FINGERTMP`` read returned one entry per enrolled
        finger, each carrying the user's device UID, the finger index and the
        template length, and the UIDs matched the 120-byte user records exactly.

        Template contents are discarded inside
        :func:`~clockmanager.protocol.records.parse_fingerprint_payload` and
        never reach this method's return value, a log or an export
        (``SECURITY.md``). This is a read; it enrols nothing and deletes
        nothing, and no fingerprint write operation exists in this adapter.
        """
        self.capabilities.require(Capability.READ_FINGERPRINT)

        def _read(transport: Any) -> list[FingerprintSlot]:
            payload, _size = transport.read_with_buffer(CMD_DB_RRQ, FCT_FINGERTMP)
            return parse_fingerprint_payload(payload)

        slots: list[FingerprintSlot] = self._call(_read, description="Reading fingerprint slots")
        _logger.info(
            "Enumerated fingerprint slots",
            extra={"device": self._settings.name, "slot_count": len(slots)},
        )
        return slots

    def read_storage(self) -> DeviceStorage:
        """Read what the device reports about its own capacity and usage.

        ``CMD_GET_FREE_SIZES`` was already being called for the record counts;
        this reads the rest of the same response. Capacities are what turn a
        count into something an operator can act on -- "6 of 30000 records
        used" answers a question that "6 records" does not.

        The response field ``pyzk`` labels ``cards`` is deliberately not
        surfaced. PHASE 15 found it did not change when a user was added and
        nothing establishes what it counts, so it is omitted rather than shown
        under a name that may be wrong.
        """
        self.capabilities.require(Capability.READ_STORAGE)
        storage: DeviceStorage = self._call(
            _storage_from_transport, description="Reading device storage"
        )
        return storage

    def read_device_options(self, names: Sequence[str] | None = None) -> list[DeviceOption]:
        """Read named settings from the device. Read-only, and allow-listed.

        Each option is one ``CMD_OPTIONS_RRQ`` round trip carrying a
        NUL-terminated name; the device answers ``Name=Value`` (PHASE 15). A
        name the firmware does not have is refused with code 4999, which is
        harmless and is reported as :attr:`DeviceOption.answered` being false
        rather than raised -- "this model has no work codes" is a useful answer.

        ``names`` selects a subset of :data:`NG_MB1_OPTIONS`; a name outside
        that catalogue is not read. The allow-list is what stops this becoming
        a way to fish for arbitrary named values, and a name that could carry a
        credential is refused outright (``SECURITY.md``).

        There is no counterpart that writes an option, deliberately.
        """
        self.capabilities.require(Capability.READ_DEVICE_OPTIONS)
        specs = option_specs(list(names) if names is not None else None)
        for spec in specs:
            if is_sensitive_option_name(spec.name):  # pragma: no cover - guarded catalogue
                raise DeviceValidationError(
                    f"Option {spec.name!r} could carry a credential and is never read."
                )

        def _read(transport: Any) -> list[DeviceOption]:
            return [self._read_one_option(transport, spec) for spec in specs]

        options: list[DeviceOption] = self._call(_read, description="Reading device options")
        _logger.info(
            "Read device options",
            extra={
                "device": self._settings.name,
                "requested": len(specs),
                "answered": sum(1 for option in options if option.answered),
            },
        )
        return options

    def _read_one_option(self, transport: Any, spec: DeviceOptionSpec) -> DeviceOption:
        """Ask for one option, treating a refusal as an answer, not a failure."""
        sender = getattr(transport, "send_command", None)
        if sender is None:
            sender = _private(transport, "send_command")
        response = sender(
            CMD_OPTIONS_RRQ, spec.name.encode("ascii") + b"\x00", OPTION_RESPONSE_BYTES
        )
        if not response.get("status"):
            _logger.debug(
                "Device does not support option",
                extra={"device": self._settings.name, "option": spec.name},
            )
            return DeviceOption(name=spec.name, label=spec.label, group=spec.group, note=spec.note)
        data = _private(transport, "data")
        return DeviceOption(
            name=spec.name,
            label=spec.label,
            group=spec.group,
            value=parse_device_option_response(bytes(data), name=spec.name),
            note=spec.note,
        )

    def read_operation_log(self) -> list[OperationLogEntry]:
        """Read the device's own record of what was done at the keypad.

        This is not the application's audit trail and does not overlap with it.
        The audit trail records what this application did; the device records
        enrolments, deletions and administrator menu access performed by
        somebody standing at the clock, and nothing here could see them before.

        The read is proven on the real NG-MB1 (PHASE 15); within a record only
        the timestamp is, so
        :func:`~clockmanager.protocol.records.parse_operation_log_payload`
        reports operation codes as numbers rather than naming them. Read-only:
        the log is never cleared from here.
        """
        self.capabilities.require(Capability.READ_OPERATION_LOG)

        def _read(transport: Any) -> list[OperationLogEntry]:
            payload, _size = transport.read_with_buffer(CMD_DB_RRQ, FCT_OPLOG)
            return parse_operation_log_payload(payload)

        entries: list[OperationLogEntry] = self._call(
            _read, description="Reading device operation log"
        )
        _logger.info(
            "Read device operation log",
            extra={"device": self._settings.name, "entry_count": len(entries)},
        )
        return entries

    # -- writes ---------------------------------------------------------------

    def read_raw_user_records(self) -> list[RawUserRecord]:
        """Read every user record whole, credential region included.

        Protocol-internal. The result must never be returned to the service or
        GUI layers: use :meth:`get_users`, which discards credential bytes.
        Diagnostics redacts each record inside :mod:`clockmanager.protocol.trace`
        before anything leaves this layer.
        """
        self.capabilities.require(Capability.READ_USERS)

        def _read(transport: Any) -> list[RawUserRecord]:
            payload, _size = transport.read_with_buffer(CMD_USERTEMP_RRQ, FCT_USER)
            return parse_raw_user_records(payload)

        records: list[RawUserRecord] = self._call(_read, description="Reading user records")
        return records

    def read_raw_attendance_payload(self) -> tuple[bytes, int]:
        """Read the raw attendance payload and the device's record count.

        Protocol-internal. Attendance bytes hold no credentials, so
        diagnostics may preview them; parsing still goes through the
        application-owned parser. Returns ``(payload, record_count)``.
        """
        self.capabilities.require(Capability.READ_ATTENDANCE)

        def _read(transport: Any) -> tuple[bytes, int]:
            transport.read_sizes()
            record_count = _as_count(getattr(transport, "records", None)) or 0
            payload, _size = transport.read_with_buffer(CMD_ATTLOG_RRQ)
            return payload, record_count

        result: tuple[bytes, int] = self._call(_read, description="Reading raw attendance")
        return result

    def next_available_uid(self) -> int:
        """The lowest UID not currently in use, for a newly created user."""
        return _first_free_uid({record.uid for record in self.read_raw_user_records()})

    def apply_user_write(self, draft: UserDraft) -> UserWriteOutcome:
        """Create or update one user, verifying the result before returning.

        The sequence is fixed here so a caller cannot perform it partially:
        read, validate, build, send, verify the acknowledgement, read back and
        compare. Any step that fails raises; only a fully verified write
        returns an outcome for the caller to audit.
        """
        self.capabilities.require(Capability.WRITE_USERS)
        if draft.changes_credential:
            self.capabilities.require(Capability.WRITE_USER_PASSWORD)

        draft = draft.normalised()
        problems = draft.validate(encoding=self._settings.encoding)
        if problems:
            raise DeviceValidationError(" ".join(problems))

        # 1. read the device
        existing_records = self.read_raw_user_records()
        by_uid = {record.uid: record for record in existing_records}
        current = None if draft.device_uid is None else by_uid.get(draft.device_uid)

        # 2. validate the draft against what is actually on the device
        uid = self._resolve_target_uid(draft, existing_records, current)
        before = (
            None
            if current is None
            else parse_user_record(current.raw, encoding=self._settings.encoding)
        )

        # 3. build the exact 120-byte record
        record = build_user_record(
            uid=uid,
            user_id=draft.user_id,
            first_name=draft.first_name,
            last_name=draft.last_name,
            privilege=draft.privilege,
            credential_action=draft.credential_action,
            existing=current,
            password=draft.password,
            encoding=self._settings.encoding,
        )

        # 4/5. send, and require the device to acknowledge it
        self._send_and_refresh(
            CMD_USER_WRQ,
            record,
            description=f"Writing user record for UID {uid}",
        )

        # 6/7. read back and compare
        stored = self._read_back_record(uid)
        self._compare_records(sent=record, stored=stored.raw, uid=uid)

        after = parse_user_record(stored.raw, encoding=self._settings.encoding)
        _logger.info(
            "User record written and verified",
            extra={
                "device": self._settings.name,
                "device_uid": uid,
                "user_id": after.user_id,
                # Not "created": logging reserves that attribute name.
                "record_created": current is None,
            },
        )
        return UserWriteOutcome(
            user=after,
            created=current is None,
            changes=tuple(describe_changes(before, draft)),
        )

    def _resolve_target_uid(
        self,
        draft: UserDraft,
        existing: list[RawUserRecord],
        current: RawUserRecord | None,
    ) -> int:
        """Decide which UID to write, refusing anything that would collide."""
        if draft.device_uid is not None and current is None:
            raise DeviceValidationError(
                f"No user with device UID {draft.device_uid} exists on the device. "
                "Re-read the user list before updating."
            )

        conflicting = [
            record
            for record in existing
            if record.user_id == draft.user_id and (current is None or record.uid != current.uid)
        ]
        if conflicting:
            raise DeviceValidationError(
                f"User ID {draft.user_id!r} is already used by device UID "
                f"{conflicting[0].uid}. User IDs must be unique on the device."
            )

        if current is not None:
            return current.uid
        return _first_free_uid({record.uid for record in existing})

    def delete_user(self, device_uid: int) -> DeviceUser:
        """Delete one user by device UID, verifying the removal before returning.

        Returns the user exactly as it was immediately before deletion, so the
        caller can audit what was removed.

        Attendance history already recorded on the device is NOT deleted by
        this command, and this method never attempts to clear it.
        """
        self.capabilities.require(Capability.DELETE_USERS)

        # 1. read: identify exactly what is about to be deleted
        records = {record.uid: record for record in self.read_raw_user_records()}
        target = records.get(device_uid)
        if target is None:
            raise DeviceValidationError(
                f"No user with device UID {device_uid} exists on the device. "
                "Re-read the user list before deleting."
            )
        doomed = parse_user_record(target.raw, encoding=self._settings.encoding)

        # 2/3. send, and require the device to acknowledge it
        self._send_and_refresh(
            CMD_DELETE_USER,
            pack("<H", device_uid),
            description=f"Deleting user UID {device_uid}",
        )

        # 4. read back: the record must actually be gone
        remaining = {record.uid for record in self.read_raw_user_records()}
        if device_uid in remaining:
            raise DeviceVerificationError(
                f"The device acknowledged deleting UID {device_uid}, but the record is "
                "still present after re-reading. Treat the device state as unknown."
            )

        _logger.info(
            "User deleted and verified",
            extra={
                "device": self._settings.name,
                "device_uid": device_uid,
                "user_id": doomed.user_id,
            },
        )
        return doomed

    def _send_and_refresh(self, command: int, payload: bytes, *, description: str) -> None:
        """Send one write command, require an acknowledgement, then refresh.

        ``CMD_REFRESHDATA`` makes the device reload its interior data. Without
        it a subsequent read can return the pre-write state, which would make a
        successful write look like a failed one.

        Writes are deliberately NOT retried. A retry could apply the same
        change twice, and a write whose outcome is unknown must be investigated
        rather than repeated.
        """
        transport = self._require_transport()
        try:
            send_command = _private(transport, "send_command")
            response = send_command(command, payload, 1024)
            if not response.get("status"):
                raise DeviceWriteError(
                    f"{description} was rejected by the device "
                    f"(response code {response.get('code')})."
                )
            refresh = send_command(CMD_REFRESHDATA, b"", 8)
            if not refresh.get("status"):
                raise DeviceWriteError(
                    f"{description} was accepted, but the device refused to reload its "
                    f"data (response code {refresh.get('code')}). Treat the device "
                    "state as unknown and re-read it."
                )
        except (ZKErrorConnection, ZKNetworkError) as exc:
            raise DeviceConnectionError(f"{description} lost the connection: {exc}") from exc
        except ZKErrorResponse as exc:
            raise DeviceWriteError(f"{description} was rejected by the device: {exc}") from exc
        except OSError as exc:
            raise DeviceConnectionError(f"{description} failed: {exc}") from exc

    def _read_back_record(self, uid: int) -> RawUserRecord:
        for record in self.read_raw_user_records():
            if record.uid == uid:
                return record
        raise DeviceVerificationError(
            f"The device acknowledged the write for UID {uid}, but no such record was "
            "present when reading back. Treat the device state as unknown."
        )

    def _compare_records(self, *, sent: bytes, stored: bytes, uid: int) -> None:
        """Confirm the device stored what was sent.

        This compares the record's **meaning**, not its bytes. A byte-exact
        comparison is wrong for this device, and PHASE 15 proved it on hardware:
        every write succeeded and every write then failed verification, because
        the MB1 does not store the 120 bytes it is given verbatim.

        Two device behaviours make byte equality unreachable:

        * The device writes its own value into bytes it owns
          (:data:`USER_DEVICE_FLAG_OFFSETS`). Byte 87 comes back ``0x01`` on
          every stored record no matter what was sent.
        * The device does not zero-fill a field's tail. Bytes after a field's
          NUL terminator keep whatever the slot held before, so a record whose
          slot previously held a longer name reads back with that name's
          remainder still in it. Both enrolled users on the project device
          carry such residue.

        Comparing the decoded fields is therefore both correct and stronger for
        the operator: it asserts the thing that was actually promised -- this
        user now has this ID, name and privilege -- rather than an incidental
        property of the buffer.

        The credential region is still compared on presence only. A device is
        free to store a PIN in a transformed form, so comparing those bytes
        would risk a spurious failure and would mean handling the secret.
        """
        if len(stored) != MB1_USER_RECORD_SIZE:  # pragma: no cover - guarded upstream
            raise DeviceVerificationError(
                f"Read back a {len(stored)}-byte record for UID {uid}, expected "
                f"{MB1_USER_RECORD_SIZE}."
            )

        expected = parse_user_record(sent, encoding=self._settings.encoding)
        actual = parse_user_record(stored, encoding=self._settings.encoding)

        mismatches = [
            f"{label}: sent {before!r}, device stored {after!r}"
            for label, before, after in (
                ("device UID", expected.device_uid, actual.device_uid),
                ("user ID", expected.user_id, actual.user_id),
                ("first name", expected.first_name, actual.first_name),
                ("last name", expected.last_name, actual.last_name),
                ("privilege", expected.privilege, actual.privilege),
            )
            if before != after
        ]
        if mismatches:
            raise DeviceVerificationError(
                f"The record stored for UID {uid} does not match what was sent "
                f"({'; '.join(mismatches)}). The write was acknowledged but not "
                "applied as requested; re-read the device before making further "
                "changes."
            )

        if any(sent[USER_CREDENTIAL_SLICE]) != any(stored[USER_CREDENTIAL_SLICE]):
            raise DeviceVerificationError(
                f"The credential state stored for UID {uid} does not match what was "
                "sent. Re-read the device before making further changes."
            )

    # -- live capture ---------------------------------------------------------

    def stop_live_capture(self) -> None:
        """Ask an in-progress :meth:`live_capture` to finish."""
        self._stop_live = True

    def live_capture(self, *, timeout_seconds: float = 10.0) -> Iterator[AttendanceEvent | None]:
        """Yield attendance events as they happen.

        Yields ``None`` on each idle timeout so a caller can check whether it
        has been asked to stop. Always call :meth:`stop_live_capture` to end it.
        """
        self.capabilities.require(Capability.LIVE_CAPTURE)
        transport = self._require_transport()

        sock = _private(transport, "sock")
        acknowledge = _private(transport, "ack_ok")
        was_enabled = bool(getattr(transport, "is_enabled", True))
        previous_timeout = sock.gettimeout()

        self._stop_live = False
        transport.cancel_capture()
        transport.verify_user()
        if not was_enabled:
            transport.enable_device()
        transport.reg_event(EF_ATTLOG)
        sock.settimeout(timeout_seconds)
        _logger.info("Live capture started", extra={"device": self._settings.name})

        try:
            while not self._stop_live:
                try:
                    datagram = sock.recv(LIVE_EVENT_BUFFER_BYTES)
                except TimeoutError:
                    yield None
                    continue
                except OSError as exc:
                    raise DeviceConnectionError(f"Live capture lost the connection: {exc}") from exc

                acknowledge()
                yield from self._decode_live_datagram(datagram, tcp=bool(transport.tcp))
        finally:
            sock.settimeout(previous_timeout)
            try:
                transport.reg_event(0)
                if not was_enabled:
                    transport.disable_device()
            except (ZKErrorConnection, ZKNetworkError, ZKErrorResponse, OSError) as exc:
                _logger.warning(
                    "Could not cleanly stop live capture",
                    extra={"device": self._settings.name, "error": str(exc)},
                )
            _logger.info("Live capture stopped", extra={"device": self._settings.name})

    def _decode_live_datagram(self, datagram: bytes, *, tcp: bool) -> Iterator[AttendanceEvent]:
        """Split one live datagram into events, skipping non-event traffic."""
        header_size = 16 if tcp else 8
        if len(datagram) < header_size:
            return

        command = unpack("HHHH", datagram[8:16])[0] if tcp else unpack("<4H", datagram[:8])[0]

        if command != CMD_REG_EVENT:
            _logger.debug("Ignoring non-event datagram", extra={"command": command})
            return

        body = datagram[header_size:]
        while body:
            chunk_size = _live_chunk_size(len(body))
            if chunk_size is None:
                _logger.warning(
                    "Discarding live event remainder of unrecognised length",
                    extra={"device": self._settings.name, "remaining_bytes": len(body)},
                )
                return
            # Which of the four live-event layouts this firmware sends is still
            # undetermined (PROTOCOL.md, PHASE 15): settling it needs a real
            # badge, which no amount of reading can produce. Recording the size
            # here means the next punch anyone captures answers it, at no cost
            # and with no extra tooling.
            _logger.info(
                "Live event body size observed",
                extra={
                    "device": self._settings.name,
                    "live_body_bytes": chunk_size,
                    "datagram_bytes": len(datagram),
                },
            )
            yield parse_live_event(body[:chunk_size], encoding=self._settings.encoding)
            body = body[chunk_size:]


def _live_chunk_size(remaining: int) -> int | None:
    """Pick the live-event layout that matches the remaining bytes."""
    if remaining == 12:
        return 12
    if remaining == 32:
        return 32
    if remaining == 36:
        return 36
    if remaining >= 52:
        return 52
    return None


def _storage_from_transport(transport: Any) -> DeviceStorage:
    """Build a :class:`DeviceStorage` from a ``read_sizes()`` response.

    ``pyzk`` parses the response onto its own attributes and leaves them unset
    when the device did not send them, so every field here is read through
    :func:`_as_count`, which preserves "not reported" as ``None``.
    """
    transport.read_sizes()

    def _count(name: str) -> int | None:
        return _as_count(getattr(transport, name, None))

    return DeviceStorage(
        users=StorageCounter("Users", _count("users"), _count("users_cap"), _count("users_av")),
        fingerprints=StorageCounter(
            "Fingerprints", _count("fingers"), _count("fingers_cap"), _count("fingers_av")
        ),
        attendance=StorageCounter(
            "Attendance records", _count("records"), _count("rec_cap"), _count("rec_av")
        ),
        faces=StorageCounter("Faces", _count("faces"), _count("faces_cap")),
        # pyzk calls this field "dummy". PHASE 15 found it equal to the number
        # of operation-log records the device actually returned (33 on the
        # project device), which is the only meaning anything establishes for
        # it, and it is reported under that name or not at all.
        operation_log_records=_count("dummy"),
    )


def _first_free_uid(taken: set[int]) -> int:
    """The lowest UID not already in use on the device."""
    for candidate in range(1, MAX_USER_UID + 1):
        if candidate not in taken:
            return candidate
    raise DeviceWriteError(f"The device already holds a user at every UID up to {MAX_USER_UID}.")


def _resolve_capabilities(
    *, allow_writes: bool, allow_credential_writes: bool
) -> DeviceCapabilities:
    """Apply this installation's write policy to the MB1 capability set.

    The capability set records what the *device* can do. This applies what the
    *installation* permits, which is a separate question: PHASE 15 proved the
    write path on hardware, and proving it must not be the same act as turning
    it on everywhere. Anything not explicitly unlocked here is reported as
    :data:`Support.OPERATOR_LOCKED` -- supported by the device, withheld here.
    """
    if allow_credential_writes and not allow_writes:
        raise DeviceValidationError(
            "Credential writing cannot be unlocked while user writing is locked."
        )
    unlocked: list[Capability] = []
    if allow_writes:
        unlocked.extend((Capability.WRITE_USERS, Capability.DELETE_USERS))
    if allow_credential_writes:
        unlocked.append(Capability.WRITE_USER_PASSWORD)

    still_locked = [c for c in WRITE_CAPABILITIES if c not in unlocked]
    capabilities = NG_MB1_CAPABILITIES.locked(still_locked, reason=_WRITE_LOCK_REASON)
    if not unlocked:
        return capabilities
    return capabilities.unlocked(unlocked, reason=_WRITE_UNLOCK_REASON)


def _as_text(value: Any) -> str:
    """Normalise a device string field, which pyzk may return as bytes."""
    if value is None:
        return ""
    if isinstance(value, bytes | bytearray):
        return bytes(value).split(b"\x00")[0].decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _as_count(value: Any) -> int | None:
    """Normalise a device count, preserving "not reported" as ``None``."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
