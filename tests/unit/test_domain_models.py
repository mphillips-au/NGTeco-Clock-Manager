"""Domain model tests: only verified device behaviour is encoded."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from clockmanager.domain.models import (
    AttendanceEvent,
    DeviceIdentity,
    DeviceUser,
    Privilege,
    PunchDirection,
    describe_privilege,
    describe_punch,
)


class TestPrivilege:
    def test_verified_values(self) -> None:
        assert Privilege.EMPLOYEE == 0
        assert Privilege.ADMIN == 14

    @pytest.mark.parametrize(("raw", "expected"), [(0, "Employee"), (14, "Admin")])
    def test_known_labels(self, raw: int, expected: str) -> None:
        assert describe_privilege(raw) == expected

    @pytest.mark.parametrize("raw", [1, 2, 7, 255])
    def test_unknown_privilege_is_not_guessed(self, raw: int) -> None:
        assert Privilege.from_raw(raw) is None
        assert describe_privilege(raw) == f"Unknown ({raw})"


class TestPunchDirection:
    def test_verified_values(self) -> None:
        assert PunchDirection.IN == 0
        assert PunchDirection.OUT == 1

    @pytest.mark.parametrize(("raw", "expected"), [(0, "IN"), (1, "OUT")])
    def test_known_labels(self, raw: int, expected: str) -> None:
        assert describe_punch(raw) == expected

    @pytest.mark.parametrize("raw", [2, 3, 4, 5, 255])
    def test_unknown_punch_is_not_guessed(self, raw: int) -> None:
        assert PunchDirection.from_raw(raw) is None
        assert describe_punch(raw) == f"Unknown ({raw})"


class TestDeviceUser:
    def test_display_name_prefers_full_name(self) -> None:
        user = DeviceUser(device_uid=1, user_id="1001", first_name="Ada", last_name="Lovelace")
        assert user.display_name == "Ada Lovelace"

    def test_display_name_falls_back_to_user_id(self) -> None:
        user = DeviceUser(device_uid=2, user_id="1002")
        assert user.display_name == "1002"

    def test_partial_name_is_trimmed(self) -> None:
        user = DeviceUser(device_uid=3, user_id="1003", first_name="Grace")
        assert user.display_name == "Grace"

    def test_admin_detection(self) -> None:
        employee = DeviceUser(device_uid=4, user_id="1004", privilege=0)
        admin = DeviceUser(device_uid=5, user_id="1005", privilege=14)
        assert not employee.is_admin
        assert admin.is_admin
        assert admin.privilege_label == "Admin"

    def test_no_credential_field_is_exposed(self) -> None:
        """SECURITY.md: credential/PIN data must not ride along in domain models."""
        fields = set(DeviceUser.__slots__)
        assert not fields & {"pin", "password", "card", "credential", "template"}

    @pytest.mark.parametrize(
        ("uid", "user_id"),
        [(-1, "1006"), (1, ""), (1, "   ")],
    )
    def test_invalid_user_rejected(self, uid: int, user_id: str) -> None:
        with pytest.raises(ValueError):
            DeviceUser(device_uid=uid, user_id=user_id)


class TestAttendanceEvent:
    def test_direction_comes_from_punch_only(self) -> None:
        event = AttendanceEvent(
            user_id="1001",
            occurred_at=datetime(2026, 1, 2, 8, 30, tzinfo=UTC),
            punch=0,
            status=5,
        )
        assert event.direction is PunchDirection.IN
        assert event.direction_label == "IN"
        # status is raw metadata and must be preserved untouched
        assert event.status == 5

    def test_out_punch(self) -> None:
        event = AttendanceEvent(
            user_id="1001",
            occurred_at=datetime(2026, 1, 2, 17, 0, tzinfo=UTC),
            punch=1,
        )
        assert event.direction is PunchDirection.OUT

    def test_unknown_punch_has_no_direction(self) -> None:
        event = AttendanceEvent(
            user_id="1001",
            occurred_at=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
            punch=9,
            status=0,
        )
        assert event.direction is None
        assert event.direction_label == "Unknown (9)"

    def test_empty_user_id_rejected(self) -> None:
        with pytest.raises(ValueError):
            AttendanceEvent(user_id=" ", occurred_at=datetime.now(UTC), punch=0)


class TestDeviceIdentity:
    def test_requires_name(self) -> None:
        with pytest.raises(ValueError):
            DeviceIdentity(name="  ")

    def test_observed_ng_mb1_shape(self) -> None:
        identity = DeviceIdentity(
            name="Front door clock",
            model="NG-MB1",
            platform="ZMM510_TFT",
            firmware_version="Ver 8.0.4.5-7108-02",
        )
        assert identity.serial_number is None
        assert identity.platform == "ZMM510_TFT"
