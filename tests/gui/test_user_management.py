"""GUI user-management tests.

``TESTING.md`` keeps business logic out of widgets, so these are smoke and
wiring tests: the form validates through the domain rules, the buttons reflect
whether writing is available, and the destructive path cannot run without a
confirmation.
"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from clockmanager.config import AppConfig, AppPaths
from clockmanager.domain.models import DeviceUser, Privilege
from clockmanager.domain.users import CredentialAction
from clockmanager.gui.views import AuditView, UserFormDialog, UsersView
from clockmanager.gui.views import users as users_view
from clockmanager.services.application import ApplicationContext, bootstrap
from clockmanager.services.audit import AuditAction, AuditOutcome, AuditService
from clockmanager.services.devices import DeviceProfile
from clockmanager.services.users import WriteAvailability
from tests.gui.conftest import drain


@pytest.fixture(autouse=True)
def _settle(qt_app: QApplication) -> Any:
    """Let every background worker finish before a test's widgets are freed.

    A pooled worker that outlives its view emits a signal into a destroyed
    widget, which crashes Qt rather than failing a test.
    """
    yield
    drain(qt_app)
    drain(qt_app)


@pytest.fixture
def writable_context(tmp_path: Any) -> Any:
    """A mock-backed context with device writing switched on."""
    config = AppConfig(
        paths=AppPaths(tmp_path / "appdata"),
        log_to_console=False,
        use_mock_device=True,
        enable_device_writes=True,
        enable_credential_writes=True,
    )
    context = bootstrap(config=config)
    context.devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))
    try:
        yield context
    finally:
        context.shutdown()


class TestUserFormDialog:
    def test_offers_only_device_verified_privileges(self, qt_app: QApplication) -> None:
        """AGENTS.md: an unverified privilege must not be writable from the UI."""
        dialog = UserFormDialog(WriteAvailability(users=True, credentials=True))
        values = [dialog._privilege.itemData(i) for i in range(dialog._privilege.count())]
        assert values == [int(Privilege.EMPLOYEE), int(Privilege.ADMIN)]

    def test_pin_field_is_masked(self, qt_app: QApplication) -> None:
        from PySide6.QtWidgets import QLineEdit

        dialog = UserFormDialog(WriteAvailability(users=True, credentials=True))
        assert dialog._password.echoMode() == QLineEdit.EchoMode.Password

    def test_pin_field_is_disabled_until_set_is_chosen(self, qt_app: QApplication) -> None:
        dialog = UserFormDialog(WriteAvailability(users=True, credentials=True))
        assert not dialog._password.isEnabled()

        index = dialog._credential_action.findData(CredentialAction.SET.value)
        dialog._credential_action.setCurrentIndex(index)
        assert dialog._password.isEnabled()

    def test_credential_action_is_locked_when_pin_writing_is_off(
        self, qt_app: QApplication
    ) -> None:
        dialog = UserFormDialog(
            WriteAvailability(users=True, credentials=False, reason="PIN writing is off.")
        )
        assert not dialog._credential_action.isEnabled()
        assert "PIN writing is off." in dialog._notice.text()

    def test_an_invalid_draft_keeps_the_dialog_open(self, qt_app: QApplication) -> None:
        dialog = UserFormDialog(WriteAvailability(users=True, credentials=True))
        dialog._user_id.setText("   ")
        dialog._on_accept()

        assert dialog.result() != UserFormDialog.DialogCode.Accepted
        assert "User ID" in dialog._message.text()

    def test_a_valid_draft_is_accepted(self, qt_app: QApplication) -> None:
        dialog = UserFormDialog(WriteAvailability(users=True, credentials=True))
        dialog._user_id.setText("EMP-900")
        dialog._first_name.setText("Test")
        dialog._on_accept()

        assert dialog.result() == UserFormDialog.DialogCode.Accepted
        draft = dialog.draft()
        assert draft.user_id == "EMP-900"
        assert draft.credential_action is CredentialAction.PRESERVE
        assert draft.password is None

    def test_editing_populates_from_the_device_user_but_never_a_pin(
        self, qt_app: QApplication
    ) -> None:
        """SECURITY.md: the stored credential is never echoed into the form."""
        user = DeviceUser(
            device_uid=2,
            user_id="1002",
            first_name="Grace",
            last_name="Hopper",
            privilege=int(Privilege.ADMIN),
            has_credential_data=True,
        )
        dialog = UserFormDialog(WriteAvailability(users=True, credentials=True), user=user)

        assert dialog._user_id.text() == "1002"
        assert dialog._privilege.currentData() == int(Privilege.ADMIN)
        assert dialog._password.text() == ""
        assert dialog.draft().device_uid == 2


class TestUsersViewWithWritesDisabled:
    def test_write_buttons_are_disabled_and_explained(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = UsersView(configured_context.devices, configured_context.users)
        view.load()
        drain(qt_app)

        assert not view._add_button.isEnabled()
        assert not view._edit_button.isEnabled()
        assert not view._delete_button.isEnabled()
        assert "switched off" in view._write_notice.text()


class TestUsersViewWithWritesEnabled:
    def _loaded(self, qt_app: QApplication, context: ApplicationContext) -> UsersView:
        view = UsersView(context.devices, context.users)
        view.load()
        drain(qt_app)
        return view

    def test_add_is_available_and_delete_needs_a_selection(
        self, qt_app: QApplication, writable_context: ApplicationContext
    ) -> None:
        view = self._loaded(qt_app, writable_context)

        assert view._add_button.isEnabled()
        assert not view._delete_button.isEnabled()

        view._table.selectRow(0)
        assert view._delete_button.isEnabled()

    def test_selection_maps_through_the_uid_not_the_row_index(
        self, qt_app: QApplication, writable_context: ApplicationContext
    ) -> None:
        """The table sorts, so the row index is not the list index."""
        view = self._loaded(qt_app, writable_context)
        view._table.sortItems(1, order=view._table.horizontalHeader().sortIndicatorOrder())
        view._table.selectRow(0)

        selected = view._selected_user()
        assert selected is not None
        assert str(selected.device_uid) == view._table.item(0, 0).text()

    def test_a_declined_confirmation_sends_nothing(
        self,
        qt_app: QApplication,
        writable_context: ApplicationContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SECURITY.md: a destructive action needs an explicit yes."""
        view = self._loaded(qt_app, writable_context)
        before = len(
            writable_context.users.list_users(writable_context.devices.first_enabled_profile())
        )

        monkeypatch.setattr(users_view, "confirm", lambda *a, **k: False)
        view._table.selectRow(0)
        view._delete_user()
        drain(qt_app)

        profile = writable_context.devices.first_enabled_profile()
        assert len(writable_context.users.list_users(profile)) == before
        assert "cancelled" in view._status.text()

    def test_confirming_a_delete_removes_the_user_and_audits_it(
        self,
        qt_app: QApplication,
        writable_context: ApplicationContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        view = self._loaded(qt_app, writable_context)

        monkeypatch.setattr(users_view, "confirm", lambda *a, **k: True)
        view._table.selectRow(0)
        selected = view._selected_user()
        assert selected is not None
        view._delete_user()
        # Two drains: the first completes the impact read, whose callback shows
        # the confirmation and starts the delete.
        drain(qt_app)
        drain(qt_app)

        profile = writable_context.devices.first_enabled_profile()
        assert all(
            user.device_uid != selected.device_uid
            for user in writable_context.users.list_users(profile)
        )

        entry = writable_context.audit.recent()[0]
        assert entry.action == AuditAction.USER_DELETE.value
        assert entry.outcome == AuditOutcome.SUCCEEDED.value
        assert entry.target == selected.user_id
        assert "Attendance history on the device was not deleted" in entry.detail
        # The view reloads the list afterwards, so the audit entry rather than
        # the transient status text is what is asserted here.

    def test_search_and_privilege_filters_narrow_the_list(
        self, qt_app: QApplication, writable_context: ApplicationContext
    ) -> None:
        view = self._loaded(qt_app, writable_context)
        total = view._table.rowCount()

        view._privilege.setCurrentIndex(view._privilege.findData("Admin"))
        admins = view._table.rowCount()
        assert 0 < admins < total

        view._privilege.setCurrentIndex(view._privilege.findData(""))
        view._filter.setText("Hopper")
        assert view._table.rowCount() == 1

    def test_pin_filter_shows_only_enrolled_users(
        self, qt_app: QApplication, writable_context: ApplicationContext
    ) -> None:
        """The sample users carry no credential data, so the filter empties
        the list rather than quietly showing everyone."""
        view = self._loaded(qt_app, writable_context)
        with_pin = [user for user in view._users if user.has_credential_data]

        view._pin_only.setChecked(True)

        assert len(view._visible) == len(with_pin)


class TestAuditView:
    def test_shows_recorded_entries(
        self, qt_app: QApplication, context: ApplicationContext
    ) -> None:
        audit = AuditService(context.database, actor="tester")
        audit.record(
            AuditAction.USER_CREATE,
            AuditOutcome.SUCCEEDED,
            detail="User ID: -> EMP-001",
            target="EMP-001",
        )

        view = AuditView(audit)
        view.refresh()
        drain(qt_app)

        assert view._table.rowCount() == 1
        assert "EMP-001" in view._table.item(0, 5).text()

    def test_filters_by_outcome(self, qt_app: QApplication, context: ApplicationContext) -> None:
        audit = AuditService(context.database, actor="tester")
        audit.record(AuditAction.USER_CREATE, AuditOutcome.SUCCEEDED, target="A")
        audit.record(AuditAction.USER_DELETE, AuditOutcome.REFUSED, target="B")

        view = AuditView(audit)
        view.refresh()
        drain(qt_app)
        assert view._table.rowCount() == 2

        view._outcome.setCurrentIndex(view._outcome.findData(AuditOutcome.REFUSED.value))
        assert view._table.rowCount() == 1

    def test_offers_no_way_to_clear_the_log(
        self, qt_app: QApplication, context: ApplicationContext
    ) -> None:
        AuditView(AuditService(context.database))
        # Only the view's own API is examined; QWidget contributes unrelated
        # names such as clearFocus.
        own = {name for name in vars(AuditView) if not name.startswith("_")}
        assert not any(word in name for name in own for word in ("clear", "delete", "purge"))
        assert "refresh" in own
