"""Web/API boundary tests (PHASE 17).

The whole API runs against the built-in mock device: no hardware, no
sockets opened by the test itself. Device I/O happens server-side through
the application services — the layering test pins that the API owns no
protocol logic.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="API tests need pip install -e .[api]")

from fastapi.testclient import TestClient

from clockmanager.api.app import create_app
from clockmanager.config import AppConfig, AppPaths
from clockmanager.services.application import ApplicationContext, bootstrap

ADMIN_PASSWORD = "Correct-Horse-9"


@pytest.fixture
def context(tmp_path: Path) -> Iterator[ApplicationContext]:
    """A mock-device context with one admin and one device profile."""
    config = AppConfig(
        paths=AppPaths(tmp_path / "appdata"),
        log_to_console=False,
        use_mock_device=True,
    )
    ctx = bootstrap(config=config)
    try:
        from clockmanager.services.devices import DeviceProfile

        ctx.auth.bootstrap_admin(username="admin", display_name="Admin", password=ADMIN_PASSWORD)
        ctx.devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))
        yield ctx
    finally:
        ctx.shutdown()


@pytest.fixture
def client(context: ApplicationContext) -> TestClient:
    return TestClient(create_app(context))


@pytest.fixture
def admin_token(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _device_id(client: TestClient, token: str) -> int:
    response = client.get("/api/devices", headers=_auth(token))
    assert response.status_code == 200
    return int(response.json()[0]["device_id"])


# -- auth ----------------------------------------------------------------------


def test_health_is_public_and_needs_no_setup(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["application"] == "NGTeco Clock Manager"
    assert body["needs_setup"] is False
    assert body["known_devices"] == 1


def test_login_rejects_bad_password_without_naming_the_reason(
    client: TestClient,
) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert response.status_code == 401


def test_unauthenticated_requests_are_refused(client: TestClient) -> None:
    assert client.get("/api/devices").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/status").status_code == 401


def test_me_and_logout(client: TestClient, admin_token: str) -> None:
    me = client.get("/api/auth/me", headers=_auth(admin_token))
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert me.json()["role"] == "admin"
    assert "password" not in me.text.lower()

    assert client.post("/api/auth/logout", headers=_auth(admin_token)).status_code == 200
    assert client.get("/api/auth/me", headers=_auth(admin_token)).status_code == 401


def test_status_names_the_signed_in_operator(client: TestClient, admin_token: str) -> None:
    response = client.get("/api/status", headers=_auth(admin_token))
    assert response.status_code == 200
    assert "admin" in response.json()["Signed in as"]


def test_setup_refused_once_accounts_exist(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/auth/setup",
        json={"username": "late", "display_name": "Late", "password": "Some-Pass-1"},
    )
    assert response.status_code == 403


def test_roles_matrix_lists_all_three_roles(client: TestClient, admin_token: str) -> None:
    response = client.get("/api/auth/roles", headers=_auth(admin_token))
    assert response.status_code == 200
    roles = {item["role"]: item for item in response.json()}
    assert set(roles) == {"admin", "office_staff", "viewer"}
    assert "devices.settings" in roles["admin"]["permissions"]
    assert "devices.settings" not in roles["office_staff"]["permissions"]
    assert roles["viewer"]["permissions"] == ["reports.export"]


# -- accounts ------------------------------------------------------------------


def test_only_admins_manage_accounts(client: TestClient, admin_token: str) -> None:
    created = client.post(
        "/api/accounts",
        headers=_auth(admin_token),
        json={
            "username": "office",
            "display_name": "Office",
            "role": "office_staff",
            "password": "Office-Pass-1",
        },
    )
    assert created.status_code == 201

    office_login = client.post(
        "/api/auth/login", json={"username": "office", "password": "Office-Pass-1"}
    )
    office_token = str(office_login.json()["access_token"])

    refused = client.get("/api/accounts", headers=_auth(office_token))
    assert refused.status_code == 403

    listed = client.get("/api/accounts", headers=_auth(admin_token))
    assert listed.status_code == 200
    assert {item["username"] for item in listed.json()} == {"admin", "office"}
    assert "password" not in listed.text.lower()


def test_admin_can_change_role_and_disable(client: TestClient, admin_token: str) -> None:
    client.post(
        "/api/accounts",
        headers=_auth(admin_token),
        json={
            "username": "temp",
            "display_name": "Temp",
            "role": "viewer",
            "password": "Temp-Pass-1",
        },
    )
    role = client.patch(
        "/api/accounts/temp/role", headers=_auth(admin_token), json={"role": "office_staff"}
    )
    assert role.status_code == 200
    assert role.json()["role"] == "office_staff"

    disabled = client.patch(
        "/api/accounts/temp/active", headers=_auth(admin_token), json={"active": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["active"] is False

    login = client.post("/api/auth/login", json={"username": "temp", "password": "Temp-Pass-1"})
    assert login.status_code == 401


# -- devices -------------------------------------------------------------------


def test_device_crud_never_exposes_the_communication_password(
    client: TestClient, admin_token: str
) -> None:
    created = client.post(
        "/api/devices",
        headers=_auth(admin_token),
        json={"name": "Second", "host": "192.0.2.11", "communication_password": 123456},
    )
    assert created.status_code == 201
    body = created.json()
    assert "communication_password" not in body
    # The boolean indicator is fine; the secret value must never appear.
    assert '"communication_password":' not in created.text
    assert body["has_communication_password"] is True
    device_id = int(body["device_id"])

    updated = client.put(
        f"/api/devices/{device_id}",
        headers=_auth(admin_token),
        json={"name": "Second", "host": "192.0.2.12"},
    )
    assert updated.status_code == 200
    assert updated.json()["host"] == "192.0.2.12"
    # Omitted on update: the stored secret survives.
    assert updated.json()["has_communication_password"] is True

    assert client.delete(f"/api/devices/{device_id}", headers=_auth(admin_token)).status_code == 200
    assert client.get(f"/api/devices/{device_id}", headers=_auth(admin_token)).status_code == 404


def test_office_staff_cannot_change_device_profiles(client: TestClient, admin_token: str) -> None:
    client.post(
        "/api/accounts",
        headers=_auth(admin_token),
        json={
            "username": "office",
            "display_name": "Office",
            "role": "office_staff",
            "password": "Office-Pass-1",
        },
    )
    office_token = str(
        client.post(
            "/api/auth/login", json={"username": "office", "password": "Office-Pass-1"}
        ).json()["access_token"]
    )
    response = client.post(
        "/api/devices",
        headers=_auth(office_token),
        json={"name": "Nope", "host": "192.0.2.99"},
    )
    assert response.status_code == 403


def test_device_statuses_are_local_reads(client: TestClient, admin_token: str) -> None:
    response = client.get("/api/devices/statuses", headers=_auth(admin_token))
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["device"]["name"] == "Bench clock"


def test_device_inspect_reports_wired_sections(client: TestClient, admin_token: str) -> None:
    device_id = _device_id(client, admin_token)
    response = client.get(f"/api/devices/{device_id}/inspect", headers=_auth(admin_token))
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["profile_name"] == "Bench clock"
    assert body["info"]["model"]
    assert set(body["storage"]) == {
        "users",
        "fingerprints",
        "attendance",
        "faces",
        "operation_log_records",
    }
    assert len(body["options"]) >= 1
    assert all("display_value" in option for option in body["options"])
    assert isinstance(body["fingerprints"], list)
    assert isinstance(body["operation_log"], list)
    assert isinstance(body["notes"], list)
    assert body["summary"]
    assert "password" not in response.text.lower()
    assert "communication_password" not in body


def test_device_inspect_unknown_device_is_404(client: TestClient, admin_token: str) -> None:
    response = client.get("/api/devices/9999/inspect", headers=_auth(admin_token))
    assert response.status_code == 404


# -- users on a device (mock) --------------------------------------------------


def test_device_users_list_carries_no_credential(client: TestClient, admin_token: str) -> None:
    device_id = _device_id(client, admin_token)
    response = client.get(f"/api/devices/{device_id}/users", headers=_auth(admin_token))
    assert response.status_code == 200
    users = response.json()
    assert len(users) >= 1
    assert "password" not in response.text.lower()
    assert set(users[0]) == {
        "device_uid",
        "user_id",
        "first_name",
        "last_name",
        "display_name",
        "privilege",
        "privilege_label",
        "has_credential_data",
    }


def test_device_writes_locked_by_default(client: TestClient, admin_token: str) -> None:
    device_id = _device_id(client, admin_token)
    response = client.post(
        f"/api/devices/{device_id}/users",
        headers=_auth(admin_token),
        json={"user_id": "ZZT-001", "first_name": "Test"},
    )
    assert response.status_code == 403


def test_device_user_write_round_trip_on_mock(tmp_path: Path) -> None:
    """Unlocked writes against the mock: create, read back, delete confirmed."""
    config = AppConfig(
        paths=AppPaths(tmp_path / "writes"),
        log_to_console=False,
        use_mock_device=True,
        enable_device_writes=True,
    )
    ctx = bootstrap(config=config)
    try:
        ctx.auth.bootstrap_admin(username="admin", display_name="A", password=ADMIN_PASSWORD)
        from clockmanager.services.devices import DeviceProfile

        profile = ctx.devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))
        assert profile.device_id is not None
        api = TestClient(create_app(ctx))
        token = str(
            api.post(
                "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
            ).json()["access_token"]
        )
        headers = _auth(token)

        created = api.post(
            f"/api/devices/{profile.device_id}/users",
            headers=headers,
            json={"user_id": "ZZT-001", "first_name": "Zed", "last_name": "Test"},
        )
        assert created.status_code == 200, created.text
        uid = int(created.json()["device_uid"])
        assert "password" not in created.text.lower()

        read = api.get(f"/api/devices/{profile.device_id}/users/{uid}", headers=headers)
        assert read.status_code == 200
        assert read.json()["user_id"] == "ZZT-001"

        unconfirmed = api.delete(f"/api/devices/{profile.device_id}/users/{uid}", headers=headers)
        assert unconfirmed.status_code == 400

        deleted = api.delete(
            f"/api/devices/{profile.device_id}/users/{uid}?confirmed=true",
            headers=headers,
        )
        assert deleted.status_code == 200
        assert (
            api.get(f"/api/devices/{profile.device_id}/users/{uid}", headers=headers).status_code
            == 404
        )
    finally:
        ctx.shutdown()


# -- attendance / sync / live --------------------------------------------------


def test_manual_sync_and_history_against_mock(client: TestClient, admin_token: str) -> None:
    device_id = _device_id(client, admin_token)
    synced = client.post(
        f"/api/devices/{device_id}/sync",
        headers=_auth(admin_token),
        json={"mode": "manual"},
    )
    assert synced.status_code == 200
    body = synced.json()
    assert body["ok"] is True
    assert body["seen"] >= 1
    assert body["new"] == body["seen"]

    again = client.post(
        f"/api/devices/{device_id}/sync",
        headers=_auth(admin_token),
        json={"mode": "manual"},
    )
    assert again.json()["new"] == 0  # duplicate-safe reconciliation

    bad_mode = client.post(
        f"/api/devices/{device_id}/sync",
        headers=_auth(admin_token),
        json={"mode": "sometimes"},
    )
    assert bad_mode.status_code == 400

    history = client.get(f"/api/devices/{device_id}/sync/history", headers=_auth(admin_token))
    assert history.status_code == 200
    assert len(history.json()) >= 2

    stored = client.get(f"/api/devices/{device_id}/attendance", headers=_auth(admin_token))
    assert stored.status_code == 200
    assert len(stored.json()) == body["seen"]

    recent = client.get("/api/attendance/recent", headers=_auth(admin_token))
    assert recent.status_code == 200
    assert len(recent.json()) >= body["seen"]


def test_viewer_cannot_trigger_sync(client: TestClient, admin_token: str) -> None:
    client.post(
        "/api/accounts",
        headers=_auth(admin_token),
        json={
            "username": "viewer",
            "display_name": "Viewer",
            "role": "viewer",
            "password": "Viewer-Pass-1",
        },
    )
    viewer_token = str(
        client.post(
            "/api/auth/login", json={"username": "viewer", "password": "Viewer-Pass-1"}
        ).json()["access_token"]
    )
    device_id = _device_id(client, admin_token)
    refused = client.post(
        f"/api/devices/{device_id}/sync",
        headers=_auth(viewer_token),
        json={"mode": "manual"},
    )
    assert refused.status_code == 403


def test_live_state_lists_devices_and_live_punches(client: TestClient, admin_token: str) -> None:
    status_response = client.get("/api/live/status", headers=_auth(admin_token))
    assert status_response.status_code == 200
    rows = status_response.json()
    assert len(rows) == 1
    assert rows[0]["device_name"] == "Bench clock"
    assert set(rows[0]) == {
        "device_id",
        "device_name",
        "stored_events",
        "live_events_stored",
        "last_success_at",
        "last_outcome",
        "last_error",
    }

    recent = client.get("/api/live/recent", headers=_auth(admin_token))
    assert recent.status_code == 200
    assert isinstance(recent.json(), list)


# -- employees / schedules / timesheets ----------------------------------------


def test_employee_lifecycle(client: TestClient, admin_token: str) -> None:
    created = client.post(
        "/api/employees",
        headers=_auth(admin_token),
        json={"user_id": "EMP-1", "first_name": "Ada", "last_name": "Lovelace"},
    )
    assert created.status_code == 201
    employee_id = int(created.json()["employee_id"])

    duplicate = client.post(
        "/api/employees",
        headers=_auth(admin_token),
        json={"user_id": "EMP-1", "first_name": "Other"},
    )
    assert duplicate.status_code == 400

    linked = client.post(
        f"/api/employees/{employee_id}/links",
        headers=_auth(admin_token),
        json={"device_id": 1, "user_id": "1"},
    )
    assert linked.status_code == 200
    assert "1" in linked.json()["device_user_ids"]

    deactivated = client.post(
        f"/api/employees/{employee_id}/active",
        headers=_auth(admin_token),
        json={"active": False},
    )
    assert deactivated.json()["active"] is False

    assert (
        client.get(f"/api/employees/{employee_id}", headers=_auth(admin_token)).status_code == 200
    )
    assert client.get("/api/employees/9999", headers=_auth(admin_token)).status_code == 404


def test_payroll_admin_only_and_timesheet_builds(client: TestClient, admin_token: str) -> None:
    from datetime import UTC, datetime

    schedule = client.post(
        "/api/schedules",
        headers=_auth(admin_token),
        json={
            "name": "Weekly",
            "schedule_type": "weekly",
            "anchor_date": "2026-01-05",
            "timezone": "Australia/Sydney",
        },
    )
    assert schedule.status_code == 201
    assert schedule.json()["is_active"] is True

    bad_type = client.post(
        "/api/schedules",
        headers=_auth(admin_token),
        json={
            "name": "Bad",
            "schedule_type": "fortnightly",
            "anchor_date": "2026-01-05",
        },
    )
    assert bad_type.status_code == 400

    employee = client.post(
        "/api/employees",
        headers=_auth(admin_token),
        json={"user_id": "EMP-9", "first_name": "Grace"},
    )
    employee_id = int(employee.json()["employee_id"])
    today = datetime.now(UTC).date().isoformat()
    sheet = client.get(
        "/api/timesheets",
        headers=_auth(admin_token),
        params={"employee_id": employee_id, "start": "2026-01-05", "end": today},
    )
    assert sheet.status_code == 200
    assert sheet.json()["total_seconds"] == 0

    current = client.get(
        "/api/timesheets/current",
        headers=_auth(admin_token),
        params={"employee_id": employee_id},
    )
    assert current.status_code == 200

    unknown = client.get(
        "/api/timesheets",
        headers=_auth(admin_token),
        params={"employee_id": 9999, "start": "2026-01-05", "end": today},
    )
    assert unknown.status_code == 400


# -- reports / audit -----------------------------------------------------------


def test_reports_and_exports(client: TestClient, admin_token: str) -> None:
    device_id = _device_id(client, admin_token)
    client.post(
        f"/api/devices/{device_id}/sync", headers=_auth(admin_token), json={"mode": "manual"}
    )

    daily = client.get(
        "/api/reports/daily_attendance",
        headers=_auth(admin_token),
        params={"start": "2026-01-01", "end": "2026-12-31"},
    )
    assert daily.status_code == 200
    assert daily.json()["report_type"] == "daily_attendance"
    assert len(daily.json()["rows"]) >= 1

    csv_export = client.get(
        "/api/reports/daily_attendance/export",
        headers=_auth(admin_token),
        params={"start": "2026-01-01", "end": "2026-12-31", "fmt": "csv"},
    )
    assert csv_export.status_code == 200
    assert "attachment" in csv_export.headers["content-disposition"]

    missing_params = client.get("/api/reports/employee_timesheet", headers=_auth(admin_token))
    assert missing_params.status_code == 400


def test_audit_needs_view_permission(client: TestClient, admin_token: str) -> None:
    client.post(
        "/api/accounts",
        headers=_auth(admin_token),
        json={
            "username": "viewer",
            "display_name": "Viewer",
            "role": "viewer",
            "password": "Viewer-Pass-1",
        },
    )
    viewer_token = str(
        client.post(
            "/api/auth/login", json={"username": "viewer", "password": "Viewer-Pass-1"}
        ).json()["access_token"]
    )
    assert client.get("/api/audit", headers=_auth(viewer_token)).status_code == 403

    entries = client.get("/api/audit", headers=_auth(admin_token))
    assert entries.status_code == 200
    assert len(entries.json()) >= 1
    assert "password" not in entries.text.lower()

    count = client.get("/api/audit/count", headers=_auth(admin_token))
    assert count.json()["count"] >= 1
