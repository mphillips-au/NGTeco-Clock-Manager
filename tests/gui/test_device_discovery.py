"""Device discovery GUI tests (PHASE 08).

Discovery is read-only: checking, scanning and identifying never store or
change anything. Only an explicit registration with an operator-supplied name
creates a profile.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from clockmanager.domain.models import DeviceIdentity
from clockmanager.gui.views.device_settings import DeviceSettingsView
from clockmanager.services.application import ApplicationContext
from clockmanager.services.devices import DeviceProfile, DiscoveredDevice
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui


def _view(mock_context: ApplicationContext) -> DeviceSettingsView:
    mock_context.devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))
    view = DeviceSettingsView(mock_context.devices)
    return view


def test_state_label_shows_last_seen_and_sync_state(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    view = _view(mock_context)
    try:
        drain(qt_app)
        drain(qt_app)  # profiles load, then the state load follows
        text = view._state_label.text()
        assert "Bench clock" in text
        assert "stored punch" in text
    finally:
        view.close()


def test_check_identifies_without_storing(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    view = _view(mock_context)
    try:
        drain(qt_app)
        view._discover_host.setText("192.0.2.10")
        view._check_address()
        drain(qt_app)

        assert "NG-MB1" in view._discovery_status.text()
        assert view._register_button.isEnabled()
        assert view._register_name.text() != ""
        # Nothing stored yet: still exactly the one profile.
        assert [p.name for p in mock_context.devices.list_profiles()] == ["Bench clock"]
    finally:
        view.close()


def test_register_creates_a_new_profile_explicitly(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    view = _view(mock_context)
    try:
        drain(qt_app)
        view._discover_host.setText("192.0.2.99")
        view._check_address()
        drain(qt_app)

        view._register_name.setText("Found clock")
        view._register_discovered()
        drain(qt_app)
        drain(qt_app)  # registration refreshes the profile list

        names = [p.name for p in mock_context.devices.list_profiles()]
        assert names == ["Bench clock", "Found clock"]
        registered = mock_context.devices.get_profile(
            next(
                p.device_id for p in mock_context.devices.list_profiles() if p.name == "Found clock"
            )
            or 0
        )
        assert registered is not None and registered.model == "NG-MB1"
        assert registered.host == "192.0.2.99"
        # The pre-existing profile is untouched by the discovery.
        bench = next(p for p in mock_context.devices.list_profiles() if p.name == "Bench clock")
        assert bench.host == "192.0.2.10"
    finally:
        view.close()


def test_registering_an_already_stored_address_is_refused(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    """Discovery never duplicates a stored address: it says where it lives."""
    view = _view(mock_context)
    try:
        drain(qt_app)
        view._discover_host.setText("192.0.2.10")
        view._check_address()
        drain(qt_app)

        view._register_name.setText("Duplicate clock")
        view._register_discovered()
        drain(qt_app)

        assert "already stored" in view._discovery_status.text()
        assert [p.name for p in mock_context.devices.list_profiles()] == ["Bench clock"]
    finally:
        view.close()


def test_scan_reports_when_no_lan_can_be_determined(
    qt_app: QApplication, mock_context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import clockmanager.services.devices as devices_module

    monkeypatch.setattr(devices_module, "local_subnet_hosts", lambda: [])
    view = _view(mock_context)
    try:
        drain(qt_app)
        view._scan_network()
        drain(qt_app)
        assert "could not be determined" in view._discovery_status.text()
        assert mock_context.devices.list_profiles() != []  # nothing removed
        assert [p.name for p in mock_context.devices.list_profiles()] == ["Bench clock"]
    finally:
        view.close()


def test_scan_results_can_be_selected_and_registered(
    qt_app: QApplication, mock_context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import clockmanager.services.devices as devices_module

    found = [
        DiscoveredDevice(host="192.0.2.50", port=4370, reachable=False, error="refused"),
        DiscoveredDevice(
            host="192.0.2.51",
            port=4370,
            reachable=True,
            response_ms=3.0,
            identity=DeviceIdentity(name="door", model="NG-MB1", firmware_version="v8"),
        ),
    ]
    monkeypatch.setattr(devices_module, "scan_hosts", lambda *a, **k: found)
    view = _view(mock_context)
    try:
        drain(qt_app)
        view._scan_network()
        drain(qt_app)

        assert view._results.count() == 2
        assert "1 answered" in view._discovery_status.text()
        view._results.setCurrentRow(1)
        drain(qt_app)
        assert view._register_button.isEnabled()

        view._register_name.setText("Scanned clock")
        view._register_discovered()
        drain(qt_app)
        drain(qt_app)
        assert "Scanned clock" in [p.name for p in mock_context.devices.list_profiles()]
    finally:
        view.close()
