"""Opt-in integration tests against a real NG-MB1.

These NEVER run by default. ``pyproject.toml`` deselects the ``real_device``
marker, and every test here also skips unless the device address is supplied:

    CLOCKMANAGER_TEST_DEVICE_HOST=<ip> \\
    .venv/Scripts/python.exe -m pytest tests/integration -m real_device

Optional:
    CLOCKMANAGER_TEST_DEVICE_PORT      (default 4370)
    CLOCKMANAGER_TEST_DEVICE_PASSWORD  (default 0)
    CLOCKMANAGER_TEST_DEVICE_TIMEOUT   (default 10)

Every test here is READ-ONLY. ``AGENTS.md`` forbids modifying production users,
clearing attendance and sending unverified packets to an MB1, so this module
must never gain a write, delete, clear or reset. Write testing arrives in
PHASE 03 and must use disposable accounts with read-back verification.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from clockmanager.domain.models import Privilege
from clockmanager.protocol.capabilities import Capability
from clockmanager.protocol.interface import DeviceConnectionSettings
from clockmanager.protocol.mb1 import NGTecoMB1Device

pytestmark = pytest.mark.real_device

ENV_HOST = "CLOCKMANAGER_TEST_DEVICE_HOST"


def _settings_or_skip() -> DeviceConnectionSettings:
    host = os.environ.get(ENV_HOST, "").strip()
    if not host:
        pytest.skip(f"Set {ENV_HOST} to run real-device integration tests.")
    return DeviceConnectionSettings(
        name="Integration test device",
        host=host,
        port=int(os.environ.get("CLOCKMANAGER_TEST_DEVICE_PORT", "4370")),
        communication_password=int(os.environ.get("CLOCKMANAGER_TEST_DEVICE_PASSWORD", "0")),
        timeout_seconds=float(os.environ.get("CLOCKMANAGER_TEST_DEVICE_TIMEOUT", "10")),
    )


@pytest.fixture
def device() -> Iterator[NGTecoMB1Device]:
    connected = NGTecoMB1Device(_settings_or_skip())
    connected.connect()
    try:
        yield connected
    finally:
        connected.disconnect()


def test_connects_and_reports_identity(device: NGTecoMB1Device) -> None:
    info = device.get_device_info()
    assert info.identity.name
    print(f"\nDevice: {info.identity.name}")
    print(f"Platform: {info.identity.platform}")
    print(f"Firmware: {info.identity.firmware_version}")
    print(f"Users: {info.user_count}  Records: {info.attendance_count}")


def test_reads_the_device_clock(device: NGTecoMB1Device) -> None:
    assert device.get_device_time() is not None


def test_reads_users_with_the_120_byte_parser(device: NGTecoMB1Device) -> None:
    users = device.get_users()

    for user in users:
        assert user.user_id
        assert Privilege.from_raw(user.privilege) is not None or user.privilege_label.startswith(
            "Unknown"
        )

    # Never print a credential; only the presence indicator.
    for user in users[:5]:
        print(
            f"\nuid={user.device_uid} id={user.user_id} "
            f"name={user.display_name!r} privilege={user.privilege_label} "
            f"credential_present={user.has_credential_data}"
        )


def test_reads_attendance(device: NGTecoMB1Device) -> None:
    events = device.get_attendance()
    for event in events:
        assert event.user_id
        assert event.occurred_at is not None
    print(f"\nRead {len(events)} attendance record(s)")


def test_capabilities_match_the_documented_state(device: NGTecoMB1Device) -> None:
    assert device.capabilities.supports(Capability.READ_USERS)
    assert not device.capabilities.supports(Capability.WRITE_USERS)


def test_module_contains_no_write_operations() -> None:
    """A guard against a future edit turning this into a destructive suite."""
    source = __file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    for forbidden in ("set_user", "delete_user", "clear_attendance", "restart(", "poweroff"):
        occurrences = text.count(forbidden)
        # Each name appears once in the docstring/guard list above and nowhere else.
        assert occurrences <= 2, f"{forbidden!r} appears {occurrences} times"
