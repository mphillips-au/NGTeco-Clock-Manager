"""Windows platform integration helpers.

Provides startup management via HKCU Run registry key, Windows firewall /
network guidance, and the named mutexes the single-instance guard and the
installer rely on. Uses Python standard library only (no PySide6 imports) to
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
    "INSTALLER_MUTEX_NAME",
    "RUN_REGISTRY_KEY",
    "NamedMutex",
    "allow_any_foreground_window",
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
#: Held by every running GUI process. ``packaging/installer.iss`` names the
#: same mutex in ``AppMutex`` so Setup refuses to overwrite a running copy
#: (one hidden in the notification area is easy to forget about).
INSTALLER_MUTEX_NAME: Final[str] = "NGTecoClockManagerRunning"

_ERROR_ALREADY_EXISTS: Final[int] = 183
_ASFW_ANY: Final[int] = -1


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


class NamedMutex:
    """A Windows named mutex, held for as long as this object is open.

    Only its existence matters: nothing ever waits on it. Creating one that
    another process already holds still succeeds, and :attr:`already_existed`
    says so, which is how a second launch learns the first is running. The
    operating system releases it when the process exits, however it exits.

    On other platforms :meth:`create` returns ``None``.
    """

    def __init__(self, handle: int, *, already_existed: bool) -> None:
        self._handle: int | None = handle
        self.already_existed = already_existed

    @classmethod
    def create(cls, name: str) -> NamedMutex | None:
        """Create or open ``name``. ``None`` off Windows or if the call fails."""
        if not is_windows():
            return None
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        create_mutex.restype = wintypes.HANDLE
        handle = create_mutex(None, False, name)
        if not handle:
            return None
        return cls(handle, already_existed=ctypes.get_last_error() == _ERROR_ALREADY_EXISTS)

    def close(self) -> None:
        """Release this process's handle. Safe to call more than once."""
        handle, self._handle = self._handle, None
        if handle is None or not is_windows():
            return
        import ctypes

        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(handle))


def allow_any_foreground_window() -> None:
    """Let another process take the foreground from this one.

    Windows refuses a background process that tries to raise its own window
    and flashes its taskbar button instead. The process the operator just
    launched does hold the foreground, so it grants that right before asking
    the running copy to show itself. No-op off Windows.
    """
    if not is_windows():
        return
    import ctypes

    with contextlib.suppress(OSError, AttributeError):
        ctypes.WinDLL("user32").AllowSetForegroundWindow(_ASFW_ANY)
