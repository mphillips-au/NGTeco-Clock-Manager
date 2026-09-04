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

This adapter is READ-ONLY. It exposes no write, delete, clear or reset
operation, and none may be added without a verified 120-byte write path,
explicit confirmation, read-back verification and audit logging.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from struct import unpack
from types import TracebackType
from typing import Any, Self

from zk import ZK
from zk.exception import ZKErrorConnection, ZKErrorResponse, ZKNetworkError

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.models import AttendanceEvent, DeviceIdentity, DeviceInfo, DeviceUser
from clockmanager.protocol.capabilities import (
    NG_MB1_CAPABILITIES,
    Capability,
    DeviceCapabilities,
)
from clockmanager.protocol.constants import (
    CMD_ATTLOG_RRQ,
    CMD_REG_EVENT,
    CMD_USERTEMP_RRQ,
    EF_ATTLOG,
    FCT_USER,
    LIVE_EVENT_BUFFER_BYTES,
)
from clockmanager.protocol.errors import (
    DeviceConnectionError,
    DeviceNotConnectedError,
    DeviceProtocolError,
    DeviceTimeoutError,
)
from clockmanager.protocol.interface import DeviceConnectionSettings
from clockmanager.protocol.records import (
    parse_attendance_payload,
    parse_live_event,
    parse_user_payload,
)
from clockmanager.protocol.retry import RetryPolicy, call_with_retry

__all__ = ["NGTecoMB1Device", "TransportFactory"]

_logger = get_logger(__name__)

#: Builds the underlying transport. Injectable so tests never need a socket.
TransportFactory = Callable[[DeviceConnectionSettings], Any]

MODEL_NAME = "NG-MB1"


def _default_transport(settings: DeviceConnectionSettings) -> ZK:
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
        transport_factory: TransportFactory = _default_transport,
        retry_policy: RetryPolicy | None = None,
        auto_reconnect: bool = True,
    ) -> None:
        self._settings = settings
        self._transport_factory = transport_factory
        self._retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self._auto_reconnect = auto_reconnect
        self._transport: Any | None = None
        self._stop_live = False

    # -- identity -------------------------------------------------------------

    @property
    def settings(self) -> DeviceConnectionSettings:
        return self._settings

    @property
    def capabilities(self) -> DeviceCapabilities:
        return NG_MB1_CAPABILITIES

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
