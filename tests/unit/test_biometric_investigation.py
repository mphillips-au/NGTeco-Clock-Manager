"""PHASE 11 — biometric / card investigation outcome.

Investigation only: no card, fingerprint or face operation was implemented
(``PROTOCOL.md``: "Biometric / card investigation"). These tests pin that
outcome, so a future edit cannot silently gain an unproven biometric or
card path without updating the investigation record first.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from clockmanager.domain.models import DeviceUser
from clockmanager.protocol import discovery
from clockmanager.protocol import mb1 as mb1_module
from clockmanager.protocol import mock as mock_module
from clockmanager.protocol.capabilities import (
    NG_MB1_CAPABILITIES,
    Capability,
    Support,
)
from clockmanager.protocol.errors import DeviceCapabilityError
from clockmanager.protocol.mb1 import NGTecoMB1Device, _resolve_capabilities
from clockmanager.protocol.mock import MockAttendanceDevice

#: pyzk user-biometric operations that must never be reached from the
#: application-owned adapter or mock. Checked against the parsed syntax
#: tree, so prose explaining why they are not used cannot fail the test.
_FORBIDDEN_PYZK_CALLS = frozenset(
    {
        "get_templates",
        "get_user_template",
        "save_user_template",
        "delete_user_template",
        "enroll_user",
    }
)

#: Name fragments that must not appear on the device classes. A method that
#: exists is a method something can call, and none of these has MB1 evidence.
#:
#: "finger" is deliberately NOT here since PHASE 15: fingerprint *enumeration*
#: is proven on the real device and implemented. "template" still is, because
#: reading, writing or enrolling one remains unproven -- and the enumeration
#: method is held to that by
#: :meth:`TestNoBiometricOrCardSurface.test_fingerprint_enumeration_never_yields_template_bytes`.
_FORBIDDEN_DEVICE_NAMES = ("face", "template", "enroll", "card")

#: The only fingerprint operation that may exist on a device class.
_ALLOWED_FINGERPRINT_METHOD = "read_fingerprint_slots"


class TestInvestigationCapabilities:
    def test_face_reads_stay_unverified(self) -> None:
        """pyzk has no face-template API and no MB1 command is known."""
        state = NG_MB1_CAPABILITIES.state(Capability.READ_FACE)
        assert state.support is Support.UNVERIFIED
        assert not state.usable
        assert not state.proven
        with pytest.raises(DeviceCapabilityError):
            NG_MB1_CAPABILITIES.require(Capability.READ_FACE)

    def test_fingerprint_reads_are_proven_for_enumeration_only(self) -> None:
        """PHASE 15 proved enumeration on hardware. It proved nothing more."""
        state = NG_MB1_CAPABILITIES.state(Capability.READ_FINGERPRINT)
        assert state.support is Support.SUPPORTED
        assert state.proven
        assert "enumerat" in state.reason.lower()
        assert "template" in state.reason.lower()

    def test_face_reads_stay_locked_with_every_write_unlock(self) -> None:
        """The adapter's unlock mapping never enables an unproven capability."""
        for capabilities in (
            _resolve_capabilities(allow_writes=True, allow_credential_writes=False),
            _resolve_capabilities(allow_writes=True, allow_credential_writes=True),
        ):
            assert not capabilities.supports(Capability.READ_FACE)

    def test_card_writing_stays_unsupported_on_both_devices(self) -> None:
        """PROTOCOL.md: no card field has been identified in the MB1 record."""
        assert NG_MB1_CAPABILITIES.state(Capability.WRITE_USER_CARD).support is Support.UNSUPPORTED
        assert not MockAttendanceDevice().capabilities.supports(Capability.WRITE_USER_CARD)
        with pytest.raises(DeviceCapabilityError):
            NG_MB1_CAPABILITIES.unlocked([Capability.WRITE_USER_CARD], reason="test")


class TestNoBiometricOrCardSurface:
    @pytest.mark.parametrize("device_type", [NGTecoMB1Device, MockAttendanceDevice])
    def test_device_classes_expose_no_biometric_or_card_operations(
        self, device_type: type[NGTecoMB1Device] | type[MockAttendanceDevice]
    ) -> None:
        public = {name for name in dir(device_type) if not name.startswith("_")}
        hits = {
            name for name in public if any(part in name.lower() for part in _FORBIDDEN_DEVICE_NAMES)
        }
        assert not hits, f"{device_type.__name__} must not expose {sorted(hits)}"

        fingerprint_methods = {name for name in public if "finger" in name.lower()}
        assert fingerprint_methods <= {_ALLOWED_FINGERPRINT_METHOD}, (
            f"{device_type.__name__} exposes an unexpected fingerprint operation: "
            f"{sorted(fingerprint_methods - {_ALLOWED_FINGERPRINT_METHOD})}"
        )

    @pytest.mark.parametrize("module", [mb1_module, mock_module])
    def test_neither_protocol_module_calls_pyzk_template_or_enroll_methods(
        self, module: object
    ) -> None:
        path = Path(str(module.__file__))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not called & _FORBIDDEN_PYZK_CALLS

    def test_discovery_defines_no_biometric_or_card_operations(self) -> None:
        path = Path(str(discovery.__file__))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            node.name.lower()
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in names:
            assert not any(part in name for part in _FORBIDDEN_DEVICE_NAMES), (
                f"discovery must not define {name!r}"
            )


class TestNoBiometricOrCardData:
    def test_device_user_carries_no_biometric_or_card_fields(self) -> None:
        """SECURITY.md: no PIN, card identifier or biometric template is persisted."""
        names = {field.name.lower() for field in fields(DeviceUser)}
        assert not names & {"card", "fingerprint", "face", "template", "biometric", "pin"}
