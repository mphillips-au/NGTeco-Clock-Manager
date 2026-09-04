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
_FORBIDDEN_DEVICE_NAMES = ("finger", "face", "template", "enroll", "card")


class TestInvestigationCapabilities:
    def test_fingerprint_and_face_reads_stay_unverified(self) -> None:
        """No template read has been proven on the project MB1."""
        for capability in (Capability.READ_FINGERPRINT, Capability.READ_FACE):
            state = NG_MB1_CAPABILITIES.state(capability)
            assert state.support is Support.UNVERIFIED
            assert not state.usable
            assert not state.proven
            with pytest.raises(DeviceCapabilityError):
                NG_MB1_CAPABILITIES.require(capability)

    def test_biometric_reads_stay_locked_with_every_write_unlock(self) -> None:
        """The adapter's unlock mapping never enables biometric capabilities."""
        for capabilities in (
            _resolve_capabilities(allow_writes=True, allow_credential_writes=False),
            _resolve_capabilities(allow_writes=True, allow_credential_writes=True),
        ):
            for capability in (Capability.READ_FINGERPRINT, Capability.READ_FACE):
                assert not capabilities.supports(capability)

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
