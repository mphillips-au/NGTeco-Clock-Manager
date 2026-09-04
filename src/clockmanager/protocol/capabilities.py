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


@dataclass(frozen=True, slots=True)
class CapabilityState:
    """The support level of one capability, with the evidence for it."""

    support: Support
    reason: str

    @property
    def usable(self) -> bool:
        return self.support in (Support.SUPPORTED, Support.OPERATOR_ENABLED)

    @property
    def proven(self) -> bool:
        """Whether real hardware has actually demonstrated this."""
        return self.support is Support.SUPPORTED


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
            states[capability] = CapabilityState(
                Support.OPERATOR_ENABLED,
                f"{reason} Underlying state: {current.reason}",
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
            "Writing the device clock has not been exercised on real hardware."
        ),
        Capability.WRITE_USERS: _unverified(
            "An application-owned 120-byte write path exists and is covered by unit "
            "tests (PHASE 03), but no MB1 has yet accepted a record from it. Generic "
            "pyzk set_user() builds a 72-byte packet and is never used. Unlock this "
            "deliberately to verify it with a disposable test user."
        ),
        Capability.WRITE_USER_PASSWORD: _unverified(
            "The 32-byte credential region's internal layout is not known. Writing a "
            "PIN uses a candidate offset inferred from the generic ZKTeco record and "
            "must be proven on a disposable test user before it is trusted."
        ),
        Capability.WRITE_USER_CARD: _unsupported(
            "No card field has been identified in the MB1 record. Card writing stays "
            "unsupported until the layout is proven (PROTOCOL.md)."
        ),
        Capability.DELETE_USERS: _unverified(
            "Deletion uses the generic delete-user command with a two-byte UID "
            "payload, which is device-model independent, but destructive testing "
            "remains controlled and no MB1 deletion has been performed. Unlock this "
            "deliberately to verify it with a disposable test user."
        ),
        Capability.CLEAR_ATTENDANCE: _unsupported(
            "Clearing attendance is destructive and is never performed automatically."
        ),
        Capability.READ_FINGERPRINT: _unverified(
            "Fingerprint template handling has not been reverse engineered."
        ),
        Capability.READ_FACE: _unverified(
            "Face template handling has not been reverse engineered."
        ),
    }
)
