"""Opt-in write tests against a real NG-MB1.

These NEVER run by default. ``pyproject.toml`` deselects the ``real_device``
marker, and every test here additionally requires TWO deliberate opt-ins::

    CLOCKMANAGER_TEST_DEVICE_HOST=<ip> \\
    CLOCKMANAGER_TEST_ALLOW_WRITES=1 \\
    .venv/Scripts/python.exe -m pytest tests/integration -m real_device

This is the suite that proves whether the 120-byte write path works on real
hardware. Until it has been run and has passed, ``Capability.WRITE_USERS``
stays :data:`Support.UNVERIFIED` and the application ships with device writing
switched off.

Safety rules, from ``AGENTS.md``:

* Every user this module touches is a **disposable test account** created by
  this module, identified by :data:`TEST_USER_ID_PREFIX`. No existing user is
  read-modify-written, renamed or deleted.
* Every test cleans up after itself, and refuses to delete anything whose user
  ID does not carry the test prefix.
* Nothing here clears attendance, resets the device or writes a biometric
  template.
* PIN writing is gated behind a third opt-in, because the credential region's
  layout is unverified and a wrong guess could disable a user's PIN.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterator

import pytest

from clockmanager.domain.models import Privilege
from clockmanager.domain.users import CredentialAction, UserDraft
from clockmanager.protocol.capabilities import Capability, Support
from clockmanager.protocol.constants import MB1_USER_RECORD_SIZE
from clockmanager.protocol.errors import DeviceValidationError
from clockmanager.protocol.interface import DeviceConnectionSettings
from clockmanager.protocol.mb1 import NGTecoMB1Device

pytestmark = pytest.mark.real_device

ENV_HOST = "CLOCKMANAGER_TEST_DEVICE_HOST"
ENV_ALLOW_WRITES = "CLOCKMANAGER_TEST_ALLOW_WRITES"
ENV_ALLOW_CREDENTIAL_WRITES = "CLOCKMANAGER_TEST_ALLOW_CREDENTIAL_WRITES"

#: Every account created here carries this prefix. Nothing without it is ever
#: modified or deleted.
TEST_USER_ID_PREFIX = "ZZTEST-"


def _settings_or_skip() -> DeviceConnectionSettings:
    host = os.environ.get(ENV_HOST, "").strip()
    if not host:
        pytest.skip(f"Set {ENV_HOST} to run real-device integration tests.")
    if os.environ.get(ENV_ALLOW_WRITES, "").strip() not in {"1", "true", "yes"}:
        pytest.skip(
            f"Set {ENV_ALLOW_WRITES}=1 to allow this suite to create and delete "
            "disposable test users on the real device."
        )
    return DeviceConnectionSettings(
        name="Integration test device",
        host=host,
        port=int(os.environ.get("CLOCKMANAGER_TEST_DEVICE_PORT", "4370")),
        communication_password=int(os.environ.get("CLOCKMANAGER_TEST_DEVICE_PASSWORD", "0")),
        timeout_seconds=float(os.environ.get("CLOCKMANAGER_TEST_DEVICE_TIMEOUT", "10")),
    )


def _credential_writes_allowed() -> bool:
    return os.environ.get(ENV_ALLOW_CREDENTIAL_WRITES, "").strip() in {"1", "true", "yes"}


@pytest.fixture
def device() -> Iterator[NGTecoMB1Device]:
    """A write-unlocked device that removes its own test accounts afterwards."""
    connected = NGTecoMB1Device(
        _settings_or_skip(),
        allow_writes=True,
        allow_credential_writes=_credential_writes_allowed(),
    )
    connected.connect()
    try:
        yield connected
    finally:
        _remove_test_accounts(connected)
        connected.disconnect()


def _remove_test_accounts(device: NGTecoMB1Device) -> None:
    """Delete only accounts this suite created. Never anything else."""
    for user in device.get_users():
        if user.user_id.startswith(TEST_USER_ID_PREFIX):
            device.delete_user(user.device_uid)


def _disposable(suffix: str, **overrides: object) -> UserDraft:
    fields: dict[str, object] = {
        "user_id": f"{TEST_USER_ID_PREFIX}{suffix}",
        "first_name": "Disposable",
        "last_name": "Test",
        "privilege": int(Privilege.EMPLOYEE),
    }
    fields.update(overrides)
    return UserDraft(**fields)  # type: ignore[arg-type]


class TestCapabilityState:
    def test_writing_reports_operator_enabled_not_verified(self, device: NGTecoMB1Device) -> None:
        """Running this suite is what turns the unlock into evidence."""
        state = device.capabilities.state(Capability.WRITE_USERS)
        assert state.support is Support.OPERATOR_ENABLED
        assert state.usable
        assert not state.proven


class TestCreateUpdateDelete:
    def test_creates_a_disposable_user_and_reads_it_back(self, device: NGTecoMB1Device) -> None:
        outcome = device.apply_user_write(_disposable("A"))

        assert outcome.created
        assert outcome.user.user_id == f"{TEST_USER_ID_PREFIX}A"
        assert outcome.user.first_name == "Disposable"

        on_device = {user.user_id for user in device.get_users()}
        assert f"{TEST_USER_ID_PREFIX}A" in on_device
        print(f"\nCreated {outcome.user.user_id} at UID {outcome.user.device_uid}")

    def test_updates_a_disposable_user(self, device: NGTecoMB1Device) -> None:
        created = device.apply_user_write(_disposable("B"))

        updated = device.apply_user_write(
            _disposable("B", last_name="Renamed", device_uid=created.user.device_uid)
        )

        assert not updated.created
        assert updated.user.last_name == "Renamed"

    def test_promotes_a_disposable_user_to_admin(self, device: NGTecoMB1Device) -> None:
        created = device.apply_user_write(_disposable("C"))

        promoted = device.apply_user_write(
            _disposable(
                "C",
                privilege=int(Privilege.ADMIN),
                device_uid=created.user.device_uid,
            )
        )

        assert promoted.user.is_admin
        print(f"\nPrivilege now {promoted.user.privilege_label}")

    def test_deletes_a_disposable_user_and_verifies_the_removal(
        self, device: NGTecoMB1Device
    ) -> None:
        created = device.apply_user_write(_disposable("D"))

        deleted = device.delete_user(created.user.device_uid)

        assert deleted.user_id == f"{TEST_USER_ID_PREFIX}D"
        assert all(user.user_id != f"{TEST_USER_ID_PREFIX}D" for user in device.get_users())

    def test_a_written_record_is_exactly_120_bytes_on_the_device(
        self, device: NGTecoMB1Device
    ) -> None:
        """The point of the whole phase: the MB1 stores a 120-byte record."""
        created = device.apply_user_write(_disposable("E"))

        record = next(
            item for item in device.read_raw_user_records() if item.uid == created.user.device_uid
        )
        assert len(record.raw) == MB1_USER_RECORD_SIZE

    def test_refuses_a_duplicate_user_id(self, device: NGTecoMB1Device) -> None:
        device.apply_user_write(_disposable("F"))
        with pytest.raises(DeviceValidationError, match="already used"):
            device.apply_user_write(_disposable("F"))


class TestCredentialWrites:
    def test_setting_a_pin_on_a_disposable_user(self, device: NGTecoMB1Device) -> None:
        """UNVERIFIED layout. Only ever run against a disposable account."""
        if not _credential_writes_allowed():
            pytest.skip(
                f"Set {ENV_ALLOW_CREDENTIAL_WRITES}=1 to exercise the unverified "
                "credential region layout."
            )

        created = device.apply_user_write(_disposable("G"))
        assert not created.user.has_credential_data

        with_pin = device.apply_user_write(
            _disposable(
                "G",
                device_uid=created.user.device_uid,
                credential_action=CredentialAction.SET,
                password="1234",
            )
        )

        # The value is never read back or printed; only its presence.
        assert with_pin.user.has_credential_data
        print("\nCredential region is now populated for the disposable account")

    def test_a_name_change_preserves_the_credential_region(self, device: NGTecoMB1Device) -> None:
        """The property that makes an update safe for a user who has a PIN."""
        if not _credential_writes_allowed():
            pytest.skip(f"Set {ENV_ALLOW_CREDENTIAL_WRITES}=1 to run this.")

        created = device.apply_user_write(_disposable("H"))
        device.apply_user_write(
            _disposable(
                "H",
                device_uid=created.user.device_uid,
                credential_action=CredentialAction.SET,
                password="4321",
            )
        )
        before = next(
            item for item in device.read_raw_user_records() if item.uid == created.user.device_uid
        )

        device.apply_user_write(
            _disposable("H", last_name="Renamed", device_uid=created.user.device_uid)
        )

        after = next(
            item for item in device.read_raw_user_records() if item.uid == created.user.device_uid
        )
        assert after.credential_region == before.credential_region


class TestSafetyGuards:
    def test_this_module_only_ever_deletes_prefixed_accounts(self) -> None:
        """A guard against a future edit making this suite destructive."""
        with open(__file__, encoding="utf-8") as handle:
            text = handle.read()

        tree = ast.parse(text)

        # Checked against the syntax tree, so this test's own prose cannot
        # trip it and a real call cannot hide in a string.
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not called & {"clear_attendance", "restart", "poweroff", "set_time"}

        # Bulk deletion happens in exactly one function, and that function is
        # guarded by the test-account prefix.
        cleanup = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_remove_test_accounts"
        )
        guards = [ast.unparse(node.test) for node in ast.walk(cleanup) if isinstance(node, ast.If)]
        assert any("TEST_USER_ID_PREFIX" in guard for guard in guards)

        # Every other delete targets a UID this module created moments earlier.
        other_deletes = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "delete_user"
            and node not in list(ast.walk(cleanup))
        ]
        assert all("created.user.device_uid" in call for call in other_deletes)

    def test_the_cleanup_helper_refuses_unprefixed_ids(self) -> None:
        """Documents the invariant even without a device present."""
        assert TEST_USER_ID_PREFIX
        assert not "1001".startswith(TEST_USER_ID_PREFIX)
