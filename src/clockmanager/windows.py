"""Windows platform integration helpers.

Provides startup management via HKCU Run registry key and Windows firewall /
network guidance. Uses Python standard library only (no PySide6 imports) to
maintain architectural layering.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Any, Final

__all__ = [
    "APP_REGISTRY_NAME",
    "DEVICE_TCP_PORT",
    "DEVICE_UDP_PORT",
    "RUN_REGISTRY_KEY",
    "get_firewall_guidance",
    "get_startup_command",
    "is_startup_enabled",
    "is_windows",
    "set_startup_enabled",
]

APP_REGISTRY_NAME: Final[str] = "NGTecoClockManager"
RUN_REGISTRY_KEY: Final[str] = r"Software\Microsoft\Windows\CurrentVersion\Run"
DEVICE_TCP_PORT: Final[int] = 4370
DEVICE_UDP_PORT: Final[int] = 4370


def is_windows() -> bool:
    """Return True if running on Windows."""
    return os.name == "nt"


def get_startup_command() -> str | None:
    """Return the currently registered startup command line, or None if not registered."""
    if not is_windows():
        return None

    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_REGISTRY_KEY,
            0,
            winreg.KEY_READ,
        ) as key:
            val, _ = winreg.QueryValueEx(key, APP_REGISTRY_NAME)
            return str(val) if val else None
    except OSError:
        return None


def is_startup_enabled() -> bool:
    """Return True if the application is set to launch on Windows login."""
    return get_startup_command() is not None


def set_startup_enabled(
    enabled: bool,
    exe_path: Path | str | None = None,
) -> bool:
    """Enable or disable launching at Windows user startup (HKCU).

    Parameters
    ----------
    enabled:
        True to add to startup, False to remove.
    exe_path:
        Path to the executable to launch. If omitted, sys.executable is used.

    Returns
    -------
    bool:
        True if the registry was successfully modified, False otherwise.
    """
    if not is_windows():
        return False

    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_REGISTRY_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                target = Path(exe_path or sys.executable).resolve()
                cmd = f'"{target}"'
                winreg.SetValueEx(
                    key,
                    APP_REGISTRY_NAME,
                    0,
                    winreg.REG_SZ,
                    cmd,
                )
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, APP_REGISTRY_NAME)
        return True
    except OSError:
        return False


def get_firewall_guidance() -> dict[str, Any]:
    """Return structured firewall and network configuration recommendations."""
    return {
        "ports": [
            {
                "port": DEVICE_TCP_PORT,
                "protocol": "TCP",
                "direction": "Outbound",
                "purpose": "Device connection and management",
                "note": "Used for sending commands and receiving attendance data from the clock.",
            },
            {
                "port": DEVICE_UDP_PORT,
                "protocol": "UDP",
                "direction": "Outbound/Broadcast",
                "purpose": "Device discovery across local subnet",
                "note": "Used to find unconfigured or DHCP-assigned clocks on the local network.",
            },
        ],
        "powershell_rules": [
            (
                "New-NetFirewallRule -DisplayName 'NGTeco Clock Manager Outbound TCP 4370' "
                "-Direction Outbound -Protocol TCP -RemotePort 4370 -Action Allow"
            ),
            (
                "New-NetFirewallRule -DisplayName 'NGTeco Clock Manager Discovery UDP 4370' "
                "-Direction Outbound -Protocol UDP -RemotePort 4370 -Action Allow"
            ),
        ],
        "network_notes": [
            "The clock hardware must be reachable on the same local subnet or across a routed network without NAT.",
            "If your network uses isolated VLANs for IoT/biometric devices, ensure TCP/UDP port 4370 routing is enabled.",
            "Device discovery uses UDP broadcast which does not traverse across different subnets/routers.",
        ],
    }
