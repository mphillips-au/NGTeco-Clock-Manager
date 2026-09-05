"""The device options this application will ask an NG-MB1 for.

``CMD_OPTIONS_RRQ`` takes a NUL-terminated option name and answers
``Name=Value``. PHASE 15 established, on the real device, which names answer
and which are refused (code 4999, harmless). This module is the resulting
catalogue: a fixed allow-list, in display order, with the label and the caveat
each value needs.

Three rules govern what may be added here.

* **Read-only.** There is no ``CMD_OPTIONS_WRQ`` anywhere in this application.
  Writing an option has never been exercised on this device, and a wrong value
  for an IP address or a matching threshold is not recoverable without physical
  access to the keypad.
* **Nothing secret.** An option name that could carry a credential is refused
  by :func:`is_sensitive_option_name` before a request is built, so a future
  edit cannot quietly pull a communication key into a settings panel, a
  diagnostics export or a log (``SECURITY.md``).
* **Nothing invented.** An option that PHASE 15 saw refused is not listed here
  as though it might work, and a value whose meaning is not established carries
  a note saying so rather than a confident label.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "NG_MB1_OPTIONS",
    "DeviceOptionSpec",
    "OptionGroup",
    "is_sensitive_option_name",
    "option_specs",
]


class OptionGroup:
    """Display groupings for the options below. Presentation only."""

    IDENTITY: Final = "Identity"
    FIRMWARE: Final = "Firmware and algorithms"
    NETWORK: Final = "Network (as configured on the device)"
    BIOMETRICS: Final = "Biometrics"
    TERMINAL: Final = "Terminal behaviour"


@dataclass(frozen=True, slots=True)
class DeviceOptionSpec:
    """One option this application knows how to ask for."""

    name: str
    label: str
    group: str
    note: str = ""


#: Fragments that mark an option name as potentially credential-bearing. A name
#: containing any of these is never sent, whatever else is true of it.
_SENSITIVE_FRAGMENTS: Final[tuple[str, ...]] = (
    "comkey",
    "key",
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
)


def is_sensitive_option_name(name: str) -> bool:
    """Whether ``name`` could carry a credential and must never be requested.

    Deliberately broad. The cost of refusing a harmless option is that an
    operator does not see one row; the cost of reading a credential-bearing one
    is that it reaches a settings panel, a diagnostics export and a log file.
    """
    folded = name.strip().lower()
    return any(fragment in folded for fragment in _SENSITIVE_FRAGMENTS)


#: Options confirmed to answer on the project NG-MB1 (PHASE 15), in the order
#: they are shown. Every one of these is a read.
NG_MB1_OPTIONS: Final[tuple[DeviceOptionSpec, ...]] = (
    DeviceOptionSpec("~DeviceName", "Device name", OptionGroup.IDENTITY),
    DeviceOptionSpec("~SerialNumber", "Serial number", OptionGroup.IDENTITY),
    DeviceOptionSpec("~Platform", "Platform", OptionGroup.IDENTITY),
    DeviceOptionSpec(
        "~ProductTime",
        "Manufactured",
        OptionGroup.IDENTITY,
        "The device's own manufacture date, useful for an asset record.",
    ),
    DeviceOptionSpec(
        "DeviceID",
        "Device ID",
        OptionGroup.IDENTITY,
        "The device's address when several share a bus. Not its IP address.",
    ),
    DeviceOptionSpec(
        "MAC",
        "MAC address",
        OptionGroup.NETWORK,
        "Stable hardware identity: unlike the IP address, this does not change "
        "when the network does.",
    ),
    DeviceOptionSpec(
        "IPAddress",
        "IP address (stored on the device)",
        OptionGroup.NETWORK,
        "The device's stored static configuration, which is NOT necessarily the "
        "address it is answering on. On the project device this field reads "
        "192.168.1.201 while the device is reachable elsewhere (PHASE 15). Never "
        "use it to reconnect or to scan for the device.",
    ),
    DeviceOptionSpec("NetMask", "Subnet mask (stored on the device)", OptionGroup.NETWORK),
    DeviceOptionSpec("GATEIPAddress", "Gateway (stored on the device)", OptionGroup.NETWORK),
    DeviceOptionSpec(
        "~PIN2Width",
        "Maximum user ID length",
        OptionGroup.FIRMWARE,
        "The device's own stated width for a user ID. This application refuses a "
        "longer one: exceeding it in PHASE 15 produced a record that could not "
        "afterwards be deleted.",
    ),
    DeviceOptionSpec("~UserExtFmt", "Extended user format", OptionGroup.FIRMWARE),
    DeviceOptionSpec("~ZKFPVersion", "Fingerprint algorithm version", OptionGroup.BIOMETRICS),
    DeviceOptionSpec("ZKFaceVersion", "Face algorithm version", OptionGroup.BIOMETRICS),
    DeviceOptionSpec(
        "FingerFunOn", "Fingerprint reader fitted", OptionGroup.BIOMETRICS, "1 = yes."
    ),
    DeviceOptionSpec("FaceFunOn", "Face reader fitted", OptionGroup.BIOMETRICS, "1 = yes."),
    DeviceOptionSpec(
        "MThreshold",
        "Fingerprint match threshold",
        OptionGroup.BIOMETRICS,
        "The device's own matching sensitivity. Reported, never changed here.",
    ),
    DeviceOptionSpec(
        "VThreshold",
        "Face match threshold",
        OptionGroup.BIOMETRICS,
        "The device's own matching sensitivity. Reported, never changed here.",
    ),
    DeviceOptionSpec(
        "~MaxUserPhotoCount",
        "User photo capacity",
        OptionGroup.FIRMWARE,
        "The device reports a capacity for user photos. This application does not "
        "read, write or display them; no photo command is known for this model.",
    ),
    DeviceOptionSpec(
        "WorkCode",
        "Work codes enabled",
        OptionGroup.TERMINAL,
        "0 on the project device: work codes are switched off, so no punch carries one.",
    ),
    DeviceOptionSpec("MustEnroll", "Enrolment required", OptionGroup.TERMINAL),
    DeviceOptionSpec("VOLUME", "Speaker volume", OptionGroup.TERMINAL),
    DeviceOptionSpec("Language", "Language code", OptionGroup.TERMINAL),
    DeviceOptionSpec("IdleMinute", "Idle timeout (minutes)", OptionGroup.TERMINAL),
    DeviceOptionSpec(
        "LockOn",
        "Door lock duration",
        OptionGroup.TERMINAL,
        "The device carries a door-lock setting. This application controls no door "
        "and sends no unlock command.",
    ),
    DeviceOptionSpec("CompatOldFirmware", "Old-firmware compatibility", OptionGroup.FIRMWARE),
    DeviceOptionSpec("~IsOnlyRFMachine", "Card-only terminal", OptionGroup.FIRMWARE),
    DeviceOptionSpec("~SSR", "Self-service reporting", OptionGroup.FIRMWARE),
    DeviceOptionSpec("RS232BaudRate", "Serial baud rate", OptionGroup.TERMINAL),
)


def option_specs(names: tuple[str, ...] | list[str] | None = None) -> tuple[DeviceOptionSpec, ...]:
    """The specs to read: the whole catalogue, or the named subset of it.

    A name outside the catalogue is not read. The allow-list is the point: it
    is what guarantees that no caller can turn an option read into a way of
    fishing for arbitrary named values off the device.
    """
    if names is None:
        return NG_MB1_OPTIONS
    wanted = {name.strip() for name in names}
    return tuple(spec for spec in NG_MB1_OPTIONS if spec.name in wanted)
