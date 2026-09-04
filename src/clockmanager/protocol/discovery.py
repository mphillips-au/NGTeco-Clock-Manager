"""LAN discovery for NGTeco devices (PHASE 08).

Read-only by construction: probing opens a TCP connection and immediately
closes it, and identification connects, reads the device snapshot and
disconnects. No write, delete, clear or reset operation exists in this module,
and discovered devices are never modified, registered or configured here —
turning a discovery into a stored profile is an explicit operator action in
:mod:`clockmanager.services.devices`.

Everything here is synchronous and PySide6-free, so the future headless
service can scan through exactly the same code.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.models import DeviceIdentity
from clockmanager.protocol.constants import DEFAULT_PORT
from clockmanager.protocol.interface import AttendanceDevice, DeviceConnectionSettings

__all__ = [
    "DiscoveredDevice",
    "hosts_from_cidr",
    "identify_device",
    "local_subnet_hosts",
    "probe_tcp",
    "scan_hosts",
]

_logger = get_logger(__name__)

#: Refuse to expand a scan range larger than this: a /16 would schedule 65k
#: connection attempts and look like a port sweep.
MAX_SCAN_HOSTS: Final = 1024

#: Assumed prefix when deriving the local LAN from a host address.
LOCAL_PREFIX_BITS: Final = 24


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    """One address the discovery scan looked at.

    ``identity`` carries what the device reported about itself when it could
    be identified safely (``None`` when the port was closed or the handshake
    failed). It contains no credential and no address default: the operator
    still chooses the stored name explicitly.
    """

    host: str
    port: int
    reachable: bool
    response_ms: float | None = None
    identity: DeviceIdentity | None = None
    error: str = ""

    @property
    def endpoint(self) -> str:
        """``host:port``, safe to log. Contains no credential."""
        return f"{self.host}:{self.port}"

    @property
    def suggested_name(self) -> str:
        """A starting point for the stored profile name, never applied silently."""
        if self.identity is not None and self.identity.serial_number:
            return f"Clock {self.identity.serial_number}"
        return f"Clock at {self.host}"

    @property
    def summary(self) -> str:
        """One human-readable line, safe to display and to log."""
        if not self.reachable:
            return f"{self.endpoint}: no answer on TCP {self.port}"
        if self.identity is None:
            return f"{self.endpoint}: reachable, not identified ({self.error})"
        model = self.identity.model or "unknown model"
        firmware = self.identity.firmware_version or "unknown firmware"
        return f"{self.endpoint}: {model} ({firmware})"


def probe_tcp(host: str, port: int, *, timeout_seconds: float = 1.0) -> DiscoveredDevice:
    """Check whether ``host:port`` accepts a TCP connection.

    Opens a connection and immediately closes it. Never sends device
    commands and never authenticates: reachability only.
    """
    if not host.strip():
        raise ValueError("A host address is required to probe.")
    if not 1 <= port <= 65535:
        raise ValueError(f"Port must be between 1 and 65535, got {port}.")
    started = time.monotonic()
    try:
        with socket.create_connection((host.strip(), port), timeout=timeout_seconds):
            pass
    except OSError as exc:
        return DiscoveredDevice(host=host.strip(), port=port, reachable=False, error=str(exc))
    elapsed_ms = (time.monotonic() - started) * 1000.0
    return DiscoveredDevice(host=host.strip(), port=port, reachable=True, response_ms=elapsed_ms)


def hosts_from_cidr(cidr: str, *, max_hosts: int = MAX_SCAN_HOSTS) -> list[str]:
    """Expand ``cidr`` (e.g. ``"192.168.1.0/24"``) into scannable host addresses.

    Refuses ranges larger than ``max_hosts`` rather than scheduling a sweep
    of thousands of addresses. Raises ``ValueError`` for an invalid range.
    """
    try:
        network = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid network range {cidr!r}.") from exc
    hosts = [str(address) for address in network.hosts()]
    if len(hosts) > max_hosts:
        raise ValueError(
            f"Network {cidr!r} holds {len(hosts)} addresses, "
            f"more than the {max_hosts} allowed in one scan. "
            "Scan a smaller range instead."
        )
    return hosts


def local_subnet_hosts(*, prefix_bits: int = LOCAL_PREFIX_BITS) -> list[str]:
    """Best-effort host list for the local LAN, derived from local addresses.

    Returns an empty list when no usable IPv4 address can be determined —
    discovery then falls back to a manually entered address. Never raises:
    an unguessable LAN is not an error.
    """
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        return []
    results: list[str] = []
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address.strip())
        except ValueError:
            continue
        if not isinstance(parsed, ipaddress.IPv4Address) or parsed.is_loopback:
            continue
        try:
            network = ipaddress.ip_network(f"{parsed}/{prefix_bits}", strict=False)
        except ValueError:
            continue
        for host in network.hosts():
            text = str(host)
            if text != str(parsed) and text not in results:
                results.append(text)
                if len(results) >= MAX_SCAN_HOSTS:
                    return results
    return results


def scan_hosts(
    hosts: Sequence[str],
    *,
    port: int = DEFAULT_PORT,
    timeout_seconds: float = 1.0,
    max_workers: int = 64,
    probe: Callable[..., DiscoveredDevice] = probe_tcp,
) -> list[DiscoveredDevice]:
    """Probe ``hosts`` in parallel and return one result per address.

    Only addresses that answer are returned as reachable; unanswered
    addresses are reported too, so the caller can say what was tried.
    Results keep the input order. ``probe`` is injectable so tests can scan
    without opening sockets.
    """
    unique = list(dict.fromkeys(host.strip() for host in hosts if host.strip()))
    if not unique:
        return []
    workers = max(1, min(max_workers, len(unique)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="discover") as pool:
        found = list(
            pool.map(lambda host: probe(host, port, timeout_seconds=timeout_seconds), unique)
        )
    reachable = sum(1 for item in found if item.reachable)
    _logger.info(
        "LAN scan finished",
        extra={"tried": len(found), "reachable": reachable, "port": port},
    )
    return found


def identify_device(
    settings: DeviceConnectionSettings,
    *,
    build: Callable[[DeviceConnectionSettings], AttendanceDevice] | None = None,
) -> DiscoveredDevice:
    """Safely identify one reachable device: connect, read, disconnect.

    Performs no write of any kind — the adapter's read path only — and always
    disconnects, including when the device fails mid-read. A device that
    cannot be identified is reported with its error, never raised past the
    caller, so one bad address cannot fail a whole scan.
    """
    from clockmanager.protocol.mb1 import NGTecoMB1Device

    factory = build if build is not None else NGTecoMB1Device
    started = time.monotonic()
    try:
        device = factory(settings)
    except Exception as exc:  # pragma: no cover - defensive: factory misuse
        return DiscoveredDevice(
            host=settings.host, port=settings.port, reachable=False, error=str(exc)
        )
    try:
        info = device.connect()
    except Exception as exc:
        return DiscoveredDevice(
            host=settings.host, port=settings.port, reachable=False, error=str(exc)
        )
    else:
        elapsed_ms = (time.monotonic() - started) * 1000.0
        return DiscoveredDevice(
            host=settings.host,
            port=settings.port,
            reachable=True,
            response_ms=elapsed_ms,
            identity=info.identity,
        )
    finally:
        try:
            device.disconnect()
        except Exception:
            _logger.exception(
                "Disconnect after identification failed",
                extra={"endpoint": settings.endpoint},
            )
