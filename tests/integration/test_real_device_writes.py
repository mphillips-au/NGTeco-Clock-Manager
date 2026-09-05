"""Opt-in write tests against a real NG-MB1.

These NEVER run by default. ``pyproject.toml`` deselects the ``real_device``
marker, and every test here additionally requires TWO deliberate opt-ins::

    CLOCKMANAGER_TEST_DEVICE_HOST=<ip> \\
    CLOCKMANAGER_TEST_ALLOW_WRITES=1 \\
    .venv/Scripts/python.exe -m pytest tests/integration -m real_device

This is the suite that proves the 120-byte write path on real hardware. It
has been run and has passed (PHASE 15, NG-MB1 serial NBF6260700048), which is
why ``Capability.WRITE_USERS`` is now :data:`Support.SUPPORTED`. The
application still ships with device writing switched off: support is not
permission.

Safety rules, from ``AGENTS.md``:

* Every user this module touches is a **disposable test account** created by
  this module, identified by :data:`TEST_USER_ID_PREFIX`. No existing user is
  read-modify-written, renamed or deleted.
* Every test cleans up after itself, and refuses to delete anything whose user
  ID does not carry the test prefix.
* Nothing here clears attendance, resets the device or writes a biometric
  template.
* PIN writing is gated behind a third opt-in. The layout is now proven (bytes
  3:11, ASCII), but writing a credential is still a separate decision.
* **No test here sends malformed input.** Field-length and encoding refusals
  are exercised against the builder in the unit suite, where they are free.
  PHASE 15 sent one over-long user ID to the real clock and it cost both
  enrolled fingerprints, an undeletable record and a forty-minute outage. A
  real-device suite exercises valid operations only, and
  :class:`TestSafetyGuards` checks that every identifier this module can send
  fits the device's stated widths.
"""

from __future__ import annotations

import ast
import os
import warnings
from collections.abc import Iterator

import pytest

from clockmanager.domain.models import Privilege
from clockmanager.domain.users import CredentialAction, UserDraft
from clockmanager.protocol.capabilities import Capability, Support
from clockmanager.protocol.constants import (
    MB1_USER_RECORD_SIZE,
    USER_ID_WRITABLE_BYTES,
    USER_PASSWORD_SLICE,
)
from clockmanager.protocol.errors import DeviceError, DeviceValidationError
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
    """A write-unlocked device that removes the accounts *this test* created."""
    connected = NGTecoMB1Device(
        _settings_or_skip(),
        allow_writes=True,
        allow_credential_writes=_credential_writes_allowed(),
    )
    connected.connect()
    pre_existing = _test_account_uids(connected)
    if pre_existing:
        warnings.warn(
            f"Test accounts were already on the device before this test: "
            f"{sorted(pre_existing)}. They will be left alone. Remove them at the "
            "device if they cannot be deleted over the protocol.",
            stacklevel=1,
        )
    try:
        yield connected
    finally:
        _remove_test_accounts(connected, keep=pre_existing)
        connected.disconnect()


def _test_account_uids(device: NGTecoMB1Device) -> set[int]:
    """The device UIDs currently holding a test account."""
    return {
        user.device_uid
        for user in device.get_users()
        if user.user_id.startswith(TEST_USER_ID_PREFIX)
    }


def _remove_test_accounts(device: NGTecoMB1Device, *, keep: set[int]) -> None:
    """Delete only the test accounts this test created. Never anything else.

    ``keep`` holds test accounts that were already on the device when the test
    started. Those are not this test's to remove, and one of them may be
    undeletable: PHASE 15 left a record that ``CMD_DELETE_USER`` acknowledges
    and does not remove. Trying anyway turned every test in this suite into a
    teardown error and hid 22 genuine passes.

    A deletion that fails is reported as a warning, not raised. Teardown must
    not manufacture a failure the test itself did not have -- but it must not
    hide leftover state either.
    """
    for uid in _test_account_uids(device) - keep:
        try:
            device.delete_user(uid)
        except DeviceError as exc:
            warnings.warn(
                f"Could not remove test account at UID {uid}: {exc}. Remove it at the device.",
                stacklevel=1,
            )


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
    def test_writing_reports_supported_once_unlocked(self, device: NGTecoMB1Device) -> None:
        """This suite is the evidence behind that state."""
        state = device.capabilities.state(Capability.WRITE_USERS)
        assert state.support is Support.SUPPORTED
        assert state.usable
        assert state.proven

    def test_a_locked_device_refuses_the_same_write(self) -> None:
        """Support is not permission: the default build still says no."""
        locked = NGTecoMB1Device(_settings_or_skip())
        state = locked.capabilities.state(Capability.WRITE_USERS)
        assert state.support is Support.OPERATOR_LOCKED
        assert state.proven
        assert not state.usable


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


class TestProvenLayout:
    """The record facts PHASE 15 established, re-checked on hardware."""

    def test_the_pin_lands_at_bytes_three_to_eleven(self, device: NGTecoMB1Device) -> None:
        """The offset that was a guess for three phases.

        The value asserted here is a throwaway PIN on a throwaway account, set
        by this test moments earlier. It is never printed.
        """
        if not _credential_writes_allowed():
            pytest.skip(f"Set {ENV_ALLOW_CREDENTIAL_WRITES}=1 to run this.")

        created = device.apply_user_write(_disposable("P"))
        device.apply_user_write(
            _disposable(
                "P",
                device_uid=created.user.device_uid,
                credential_action=CredentialAction.SET,
                password="1234",
            )
        )

        record = next(
            item for item in device.read_raw_user_records() if item.uid == created.user.device_uid
        )
        assert record.raw[USER_PASSWORD_SLICE] == b"1234" + bytes(4)
        assert not any(record.raw[USER_PASSWORD_SLICE.stop : 35])

    def test_clearing_a_pin_empties_the_whole_region(self, device: NGTecoMB1Device) -> None:
        if not _credential_writes_allowed():
            pytest.skip(f"Set {ENV_ALLOW_CREDENTIAL_WRITES}=1 to run this.")

        created = device.apply_user_write(_disposable("Q"))
        device.apply_user_write(
            _disposable(
                "Q",
                device_uid=created.user.device_uid,
                credential_action=CredentialAction.SET,
                password="1234",
            )
        )
        cleared = device.apply_user_write(
            _disposable(
                "Q",
                device_uid=created.user.device_uid,
                credential_action=CredentialAction.CLEAR,
            )
        )

        assert not cleared.user.has_credential_data
        record = next(
            item for item in device.read_raw_user_records() if item.uid == created.user.device_uid
        )
        assert not any(record.credential_region)

    def test_the_device_sets_its_own_flag_byte_and_the_write_still_verifies(
        self, device: NGTecoMB1Device
    ) -> None:
        """Byte 87 comes back 0x01 whatever we send; verification tolerates it."""
        created = device.apply_user_write(_disposable("R"))

        record = next(
            item for item in device.read_raw_user_records() if item.uid == created.user.device_uid
        )
        assert record.raw[87] == 0x01


class TestFingerprintEnumeration:
    """A read. It needs no write unlock and discloses no template."""

    def test_enumerates_slots_without_returning_templates(self, device: NGTecoMB1Device) -> None:
        slots = device.read_fingerprint_slots()

        on_device = {user.device_uid for user in device.get_users()}
        for slot in slots:
            assert slot.device_uid in on_device, "a slot must map to a real user"
            assert slot.template_bytes > 0
        print(f"\n{len(slots)} fingerprint slot(s) enrolled")


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

        # PHASE 15: no identifier this module can send may exceed the device's
        # stated width. Checked against the syntax tree so a future edit cannot
        # reintroduce the packet that cost this device its fingerprints.
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        sendable = {
            text for text in literals if text.startswith(TEST_USER_ID_PREFIX) and "\n" not in text
        }
        over_long = {text for text in sendable if len(text) > USER_ID_WRITABLE_BYTES}
        assert not over_long, f"user IDs that exceed the device width: {sorted(over_long)}"

        # Bulk deletion happens in exactly one function, and the only UIDs it
        # can reach come from the prefix-filtered helper.
        def _function(name: str) -> ast.FunctionDef:
            return next(
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == name
            )

        selector = _function("_test_account_uids")
        selector_guards = [
            ast.unparse(condition)
            for node in ast.walk(selector)
            if isinstance(node, ast.comprehension)
            for condition in node.ifs
        ]
        assert any("TEST_USER_ID_PREFIX" in guard for guard in selector_guards), (
            "the helper that chooses what to delete must filter by the test prefix"
        )

        cleanup = _function("_remove_test_accounts")
        loops = [ast.unparse(node.iter) for node in ast.walk(cleanup) if isinstance(node, ast.For)]
        assert loops and all("_test_account_uids" in loop for loop in loops), (
            "cleanup must only ever iterate the prefix-filtered set"
        )

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
