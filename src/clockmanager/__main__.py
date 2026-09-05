"""Command-line entry point.

``clockmanager`` starts the GUI. ``clockmanager --headless`` performs the same
bootstrap without importing PySide6, which is how the future Linux/Synology
service will start and how the bootstrap is verified on machines without Qt.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clockmanager import APPLICATION_NAME, __version__
from clockmanager.config import save_config
from clockmanager.errors import ClockManagerError
from clockmanager.services.application import bootstrap
from clockmanager.windows import (
    get_firewall_guidance,
    is_startup_enabled,
    is_windows,
    set_startup_enabled,
)

__all__ = ["main"]

EXIT_OK = 0
EXIT_ERROR = 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clockmanager",
        description=f"{APPLICATION_NAME} — attendance management for NGTeco NG-MB1 devices.",
    )
    parser.add_argument("--version", action="version", version=f"{APPLICATION_NAME} {__version__}")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override the application data directory (config, database, logs).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Bootstrap configuration, logging and the database, print status, and exit.",
    )
    parser.add_argument(
        "--write-config",
        action="store_true",
        help="Write the resolved configuration to the configuration file and exit.",
    )
    parser.add_argument(
        "--enable-startup",
        action="store_true",
        help="Configure NGTeco Clock Manager to launch automatically at Windows startup.",
    )
    parser.add_argument(
        "--disable-startup",
        action="store_true",
        help="Remove NGTeco Clock Manager from Windows startup.",
    )
    parser.add_argument(
        "--status-startup",
        action="store_true",
        help="Check if NGTeco Clock Manager is configured to launch at Windows startup.",
    )
    parser.add_argument(
        "--firewall-info",
        action="store_true",
        help="Print Windows firewall and network configuration requirements for NGTeco devices.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the application. Returns a process exit code."""
    args = _build_parser().parse_args(argv)

    if args.firewall_info:
        info = get_firewall_guidance()
        print(f"{APPLICATION_NAME} — Windows Firewall & Network Guidance")
        print("=" * 60)
        print("\nRequired Ports:")
        for p in info["ports"]:
            print(f"  - {p['protocol']} Port {p['port']} ({p['direction']}): {p['purpose']}")
            print(f"    Note: {p['note']}")
        print("\nPowerShell Firewall Commands (Run as Administrator if needed):")
        for rule in info["powershell_rules"]:
            print(f"  {rule}")
        print("\nNetwork Considerations:")
        for note in info["network_notes"]:
            print(f"  - {note}")
        return EXIT_OK

    if args.enable_startup:
        if not is_windows():
            print("Startup registration is only available on Windows.", file=sys.stderr)
            return EXIT_ERROR
        if set_startup_enabled(True):
            print("Successfully registered NGTeco Clock Manager to launch at Windows startup.")
            return EXIT_OK
        print("Failed to register NGTeco Clock Manager at Windows startup.", file=sys.stderr)
        return EXIT_ERROR

    if args.disable_startup:
        if not is_windows():
            print("Startup registration is only available on Windows.", file=sys.stderr)
            return EXIT_ERROR
        if set_startup_enabled(False):
            print("Successfully removed NGTeco Clock Manager from Windows startup.")
            return EXIT_OK
        print("Failed to remove NGTeco Clock Manager from Windows startup.", file=sys.stderr)
        return EXIT_ERROR

    if args.status_startup:
        if not is_windows():
            print("Startup registration is only available on Windows.")
            return EXIT_OK
        status_str = "ENABLED" if is_startup_enabled() else "DISABLED"
        print(f"Windows startup status: {status_str}")
        return EXIT_OK

    try:
        context = bootstrap(data_dir=args.data_dir)
    except ClockManagerError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        if args.write_config:
            path = save_config(context.config)
            print(f"Configuration written to {path}")
            return EXIT_OK

        if args.headless:
            for label, value in context.status().as_rows():
                print(f"{label:<20} {value}")
            return EXIT_OK

        try:
            from clockmanager.gui import run_gui
        except ImportError as exc:
            print(
                "The graphical interface requires PySide6. Install it with "
                f"'pip install -e .[gui]', or run with --headless. ({exc})",
                file=sys.stderr,
            )
            return EXIT_ERROR

        return run_gui(context)
    except ClockManagerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        context.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
