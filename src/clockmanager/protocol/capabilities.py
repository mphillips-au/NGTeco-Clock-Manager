"""Device capability model.

``AGENTS.md``: unknown protocol behaviour must be marked unsupported until
proven. This module makes that machine-checkable — an operation whose support
is :data:`Support.UNVERIFIED` cannot be invoked by accident, and the reason it
is unverified travels with it into diagnostics and the GUI.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from clockmanager.protocol.errors import DeviceCapabilityError

__all__ = [
    "NG_MB1_CAPABILITIES",
    "WRITE_CAPABILITIES",
    "Capability",
    "CapabilityState",
    "DeviceCapabilities",
    "Support",
]


class Capability(StrEnum):
    """Operations an attendance device may support."""

    CONNECT = "connect"
    DEVICE_INFO = "device_info"
    READ_TIME = "read_time"
    READ_USERS = "read_users"
    READ_ATTENDANCE = "read_attendance"
    LIVE_CAPTURE = "live_capture"
    SET_TIME = "set_time"
    WRITE_USERS = "write_users"
    WRITE_USER_PASSWORD = "write_user_password"
    WRITE_USER_CARD = "write_user_card"
    DELETE_USERS = "delete_users"
    CLEAR_ATTENDANCE = "clear_attendance"
    READ_FINGERPRINT = "read_fingerprint"
    READ_FACE = "read_face"
    READ_DEVICE_OPTIONS = "read_device_options"
    WRITE_DEVICE_OPTIONS = "write_device_options"
    READ_STORAGE = "read_storage"
    READ_OPERATION_LOG = "read_operation_log"


class Support(StrEnum):
    """How well a capability is established for a device."""

    #: Verified against the real device.
    SUPPORTED = "supported"
    #: Known not to work, or deliberately withheld.
    UNSUPPORTED = "unsupported"
    #: Not proven on real hardware. Treated as unusable until it is.
    UNVERIFIED = "unverified"
    #: Still unproven, but deliberately unlocked by an operator so it can be
    #: verified on real hardware with disposable accounts. Usable, and labelled
    #: everywhere it appears so nobody mistakes it for a verified capability.
    OPERATOR_ENABLED = "operator-enabled (unverified)"
    #: The device is known to support this, but this installation has not
    #: enabled it. Evidence and permission are different questions: proving a
    #: write works on hardware must not, by itself, start letting every
    #: installation write. Not usable until an operator unlocks it.
    OPERATOR_LOCKED = "supported (not enabled here)"


@dataclass(frozen=True, slots=True)
class CapabilityState:
    """The support level of one capability, with the evidence for it."""

    support: Support
    reason: str

    @property
    def usable(self) -> bool:
        """Whether this device may actually be asked to do it right now."""
        return self.support in (Support.SUPPORTED, Support.OPERATOR_ENABLED)

    @property
    def proven(self) -> bool:
        """Whether real hardware has actually demonstrated this.

        Independent of :attr:`usable`. A capability can be proven and still
        locked (:data:`Support.OPERATOR_LOCKED`), or usable and still unproven
        (:data:`Support.OPERATOR_ENABLED`).
        """
        return self.support in (Support.SUPPORTED, Support.OPERATOR_LOCKED)


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    """The capability set of a device model."""

    states: Mapping[Capability, CapabilityState]

    def state(self, capability: Capability) -> CapabilityState:
        return self.states.get(
            capability,
            CapabilityState(Support.UNVERIFIED, "Not described for this device."),
        )

    def supports(self, capability: Capability) -> bool:
        return self.state(capability).usable

    def require(self, capability: Capability) -> None:
        """Raise unless ``capability`` is verified for this device."""
        state = self.state(capability)
        if not state.usable:
            raise DeviceCapabilityError(
                f"Capability {capability.value!r} is {state.support.value}: {state.reason}"
            )

    def unlocked(self, capabilities: Iterable[Capability], *, reason: str) -> DeviceCapabilities:
        """Return a copy with ``capabilities`` deliberately enabled by an operator.

        This is how an unproven write path is exercised on real hardware
        without lying about it: the capability becomes usable but is reported
        as :data:`Support.OPERATOR_ENABLED`, never as verified.

        An :data:`Support.UNSUPPORTED` capability cannot be unlocked. Those are
        withheld because they are known to be wrong or destructive, and no
        operator flag changes that.
        """
        states = dict(self.states)
        for capability in capabilities:
            current = self.state(capability)
            if current.support is Support.UNSUPPORTED:
                raise DeviceCapabilityError(
                    f"Capability {capability.value!r} is unsupported and cannot be "
                    f"unlocked: {current.reason}"
                )
            if current.support is Support.SUPPORTED:
                continue
            if current.support is Support.OPERATOR_LOCKED:
                # Proven on hardware; the lock was policy, not doubt. Unlocking
                # it restores the truth rather than labelling it unverified.
                states[capability] = CapabilityState(Support.SUPPORTED, current.reason)
                continue
            states[capability] = CapabilityState(
                Support.OPERATOR_ENABLED,
                f"{reason} Underlying state: {current.reason}",
            )
        return DeviceCapabilities(states=states)

    def locked(self, capabilities: Iterable[Capability], *, reason: str) -> DeviceCapabilities:
        """Return a copy with proven ``capabilities`` withheld from this install.

        This is the counterpart to :meth:`unlocked`, and it is what keeps a
        capability graduating to :data:`Support.SUPPORTED` from quietly turning
        writing on everywhere. Only a proven capability can be locked this way;
        anything already unusable is left exactly as it is, so locking can never
        make an unverified capability look better than it is.
        """
        states = dict(self.states)
        for capability in capabilities:
            current = self.state(capability)
            if current.support is not Support.SUPPORTED:
                continue
            states[capability] = CapabilityState(
                Support.OPERATOR_LOCKED, f"{reason} Device support: {current.reason}"
            )
        return DeviceCapabilities(states=states)

    def as_rows(self) -> list[tuple[str, str, str]]:
        """Capability/support/reason triples for diagnostics display."""
        return [
            (capability.value, self.state(capability).support.value, self.state(capability).reason)
            for capability in Capability
        ]


def _verified(reason: str) -> CapabilityState:
    return CapabilityState(Support.SUPPORTED, reason)


def _unverified(reason: str) -> CapabilityState:
    return CapabilityState(Support.UNVERIFIED, reason)


def _unsupported(reason: str) -> CapabilityState:
    return CapabilityState(Support.UNSUPPORTED, reason)


#: Capabilities of the NGTeco NG-MB1, as established in ``PROTOCOL.md`` and
#: ``RESEARCH.md``. Nothing here is aspirational.
NG_MB1_CAPABILITIES = DeviceCapabilities(
    states={
        Capability.CONNECT: _verified("TCP 4370 connection verified on the real device."),
        Capability.DEVICE_INFO: _verified("Firmware, platform, name and serial retrieved."),
        Capability.READ_TIME: _verified("Device clock read verified."),
        Capability.READ_USERS: _verified(
            "Verified using the application-owned 120-byte MB1 parser. "
            "Generic pyzk 28/72-byte parsing is incorrect for this device."
        ),
        Capability.READ_ATTENDANCE: _verified("Historical attendance retrieval verified."),
        Capability.LIVE_CAPTURE: _verified("Live attendance capture verified."),
        Capability.SET_TIME: _unverified(
            "Writing the device clock has not been exercised on real hardware. "
            "PHASE 15 found the device clock correct to within a minute, so there "
            "was no safe pretext to change it; the operation stays unproven rather "
            "than being exercised for its own sake."
        ),
        Capability.WRITE_USERS: _verified(
            "PHASE 15: the real NG-MB1 accepted application-built 120-byte records "
            "through CMD_USER_WRQ and applied them. Create, rename and privilege "
            "changes (0 and 14, both directions) were all read back correct on "
            "disposable test users. Generic pyzk set_user() builds a 72-byte packet "
            "and is still never used. Writing remains OFF by default: the capability "
            "says the device accepts a record, not that an installation should send "
            "one (CLOCKMANAGER_ENABLE_DEVICE_WRITES)."
        ),
        Capability.WRITE_USER_PASSWORD: _verified(
            "PHASE 15: the PIN is an 8-byte NUL-padded ASCII field at record bytes "
            "3:11, proven on a disposable test user. Setting '1234' stored exactly "
            "those digits and nothing else in the 32-byte region; clearing zeroed it; "
            "a name-only update preserved it. Still gated separately by "
            "CLOCKMANAGER_ENABLE_CREDENTIAL_WRITES."
        ),
        Capability.WRITE_USER_CARD: _unsupported(
            "No card field has been identified in the MB1 record. Card writing stays "
            "unsupported until the layout is proven (PROTOCOL.md)."
        ),
        Capability.DELETE_USERS: _verified(
            "PHASE 15: CMD_DELETE_USER with a two-byte little-endian UID removed "
            "disposable test users at UID 3 and UID 900 and the removal was confirmed "
            "by re-reading. One caveat is recorded in PROTOCOL.md: a record written "
            "with an over-long user ID could NOT be deleted afterwards, which is why "
            "the writable user-ID length is now bounded."
        ),
        Capability.CLEAR_ATTENDANCE: _unsupported(
            "Clearing attendance is destructive and is never performed automatically."
        ),
        Capability.READ_FINGERPRINT: _verified(
            "PHASE 15: the fingerprint store can be ENUMERATED on the real NG-MB1 "
            "via CMD_DB_RRQ/FCT_FINGERTMP -- device UID, finger index, valid flag "
            "and template length per entry, with UIDs matching the 120-byte user "
            "records. This capability covers enumeration only. Reading, writing or "
            "enrolling a template is NOT supported and no such operation exists in "
            "the adapter; template bytes are discarded inside the parser."
        ),
        Capability.READ_DEVICE_OPTIONS: _verified(
            "PHASE 15: CMD_OPTIONS_RRQ with a NUL-terminated option name answers "
            "'Name=Value' on the real NG-MB1. Read-only, fast, and harmless when "
            "the name is unknown -- the device returns code 4999 and nothing else "
            "happens. The names this application asks for are a fixed allow-list "
            "(clockmanager.protocol.options), none of which can carry a credential."
        ),
        Capability.WRITE_DEVICE_OPTIONS: _unsupported(
            "Writing a device option has never been attempted on this hardware and "
            "no CMD_OPTIONS_WRQ call exists in this application. A wrong value for "
            "an IP address or a matching threshold is not recoverable over the "
            "protocol -- PHASE 15 spent forty minutes recovering a device whose "
            "session service had stopped answering, and remote reboot needs the "
            "very session that had failed."
        ),
        Capability.READ_STORAGE: _verified(
            "PHASE 15: CMD_GET_FREE_SIZES returns capacities and free counts "
            "alongside the usage figures the application already read -- 400 "
            "fingerprint slots, 200 users, 30000 attendance records on the project "
            "device. The field pyzk labels 'cards' is deliberately NOT surfaced: it "
            "did not change when a user was added and nothing establishes what it "
            "counts."
        ),
        Capability.READ_OPERATION_LOG: _verified(
            "PHASE 15: a buffered CMD_DB_RRQ/FCT_OPLOG read returned 528 bytes for "
            "33 records on the real NG-MB1, and the device's own record count agrees. "
            "The read and the 16-byte record size are proven; within a record only "
            "the packed timestamp at bytes 4:8 is. The remaining fields are decoded "
            "positionally from the ZKTeco SDK layout and are reported as raw numbers, "
            "never as named operations."
        ),
        Capability.READ_FACE: _unverified(
            "PHASE 15: the device reports a face count and ZKFaceVersion=35 / "
            "FaceFunOn=1 through option reads, but pyzk 0.9 has no face-template "
            "API and no command, payload or structure is known for reading one. "
            "There is nothing to implement from."
        ),
    }
)


#: The capabilities that change the device and therefore stay locked unless an
#: installation deliberately enables them, however well proven they are.
WRITE_CAPABILITIES: tuple[Capability, ...] = (
    Capability.WRITE_USERS,
    Capability.DELETE_USERS,
    Capability.WRITE_USER_PASSWORD,
)
