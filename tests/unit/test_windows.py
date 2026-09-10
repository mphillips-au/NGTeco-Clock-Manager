"""Unit tests for Windows platform integration."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from clockmanager import windows
from clockmanager.__main__ import _build_parser, main


def test_is_windows() -> None:
    expected = sys.platform == "win32"
    assert windows.is_windows() == expected


def test_get_firewall_guidance() -> None:
    guidance = windows.get_firewall_guidance()
    assert "ports" in guidance
    assert len(guidance["ports"]) >= 2
    protocols = {p["protocol"] for p in guidance["ports"]}
    assert "TCP" in protocols
    assert "UDP" in guidance["ports"][1]["protocol"]
    assert any("4370" in rule for rule in guidance["powershell_rules"])
    assert any("VLAN" in note or "subnet" in note for note in guidance["network_notes"])


def test_startup_command_non_windows() -> None:
    with patch("clockmanager.windows.is_windows", return_value=False):
        assert windows.get_startup_command() is None
        assert windows.is_startup_enabled() is False
        assert windows.set_startup_enabled(True) is False


def test_startup_mock_winreg() -> None:
    mock_winreg = MagicMock()
    mock_key = MagicMock()
    mock_winreg.OpenKey.return_value.__enter__.return_value = mock_key
    mock_winreg.QueryValueEx.return_value = ('"C:\\app\\clockmanager.exe"', 1)
    mock_winreg.HKEY_CURRENT_USER = 1
    mock_winreg.KEY_READ = 1
    mock_winreg.KEY_SET_VALUE = 2
    mock_winreg.REG_SZ = 1

    with (
        patch("clockmanager.windows.is_windows", return_value=True),
        patch.dict("sys.modules", {"winreg": mock_winreg}),
    ):
        assert windows.get_startup_command() == '"C:\\app\\clockmanager.exe"'
        assert windows.is_startup_enabled() is True

        # Test enable with explicit exe
        assert windows.set_startup_enabled(True, exe_path=Path("C:/app/custom.exe"))
        mock_winreg.SetValueEx.assert_called_once()

        # Test disable
        assert windows.set_startup_enabled(False)
        mock_winreg.DeleteValue.assert_called_once()


def test_cli_firewall_info(capsys: object) -> None:
    exit_code = main(["--firewall-info"])
    assert exit_code == 0


def test_cli_startup_flags() -> None:
    parser = _build_parser()
    args = parser.parse_args(["--enable-startup"])
    assert args.enable_startup is True

    args = parser.parse_args(["--disable-startup"])
    assert args.disable_startup is True

    args = parser.parse_args(["--status-startup"])
    assert args.status_startup is True


def test_named_mutex_reports_an_existing_holder() -> None:
    name = "NGTecoClockManager-unit-test-mutex"
    first = windows.NamedMutex.create(name)
    if not windows.is_windows():
        assert first is None
        return
    assert first is not None
    assert not first.already_existed
    second = windows.NamedMutex.create(name)
    try:
        assert second is not None
        assert second.already_existed
    finally:
        if second is not None:
            second.close()
        first.close()
        first.close()  # closing twice is harmless
    third = windows.NamedMutex.create(name)
    assert third is not None
    assert not third.already_existed
    third.close()


def test_allow_any_foreground_window_never_raises() -> None:
    windows.allow_any_foreground_window()
