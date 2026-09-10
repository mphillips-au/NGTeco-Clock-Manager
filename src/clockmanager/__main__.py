"""Command-line entry point.

``clockmanager`` starts the GUI. ``clockmanager --headless`` performs the same
bootstrap without importing PySide6, which is how the future Linux/Synology
service will start and how the bootstrap is verified on machines without Qt.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from clockmanager import APPLICATION_NAME, __version__
from clockmanager.config import MIN_SERVICE_POLL_SECONDS, default_data_dir, save_config
from clockmanager.errors import ClockManagerError
from clockmanager.services.application import bootstrap
from clockmanager.windows import (
    get_firewall_guidance,
    is_startup_enabled,
    is_windows,
    set_startup_enabled,
)

if TYPE_CHECKING:
    from clockmanager.gui.single_instance import SingleInstance

__all__ = ["main", "make_console_output_safe"]

EXIT_OK = 0
EXIT_ERROR = 1


def make_console_output_safe(*streams: object) -> None:
    """Stop a legacy Windows console code page from killing the process.

    A stock ``cmd.exe`` runs on an OEM code page (cp437/cp850) that cannot
    encode the punctuation used throughout this application's messages, and
    Python raises ``UnicodeEncodeError`` from ``print`` rather than degrading.
    ``clockmanager --help`` alone was enough to crash a fresh Windows install.

    Reconfiguring the streams to replace unencodable characters is deliberate:
    an operator running a diagnostic command must get output, not a traceback.
    Streams that cannot be reconfigured (a pipe replaced by a test, a stream
    already closed) are left exactly as they are.
    """
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (OSError, ValueError):  # pragma: no cover - defensive
            continue


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
        "--serve",
        action="store_true",
        help=(
            "Run the Linux/Synology headless service: reconcile every enabled "
            "device on its own sync interval until SIGTERM/SIGINT. No GUI imports."
        ),
    )
    parser.add_argument(
        "--serve-once",
        action="store_true",
        help=(
            "Run one headless reconciliation pass over every enabled device "
            "and exit. Suitable for cron/systemd timers."
        ),
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help=(
            "Headless-service loop tick in seconds (overrides the configuration "
            f"file; minimum {MIN_SERVICE_POLL_SECONDS})."
        ),
    )
    parser.add_argument(
        "--health-bind",
        type=str,
        default=None,
        help=(
            "Headless-service health endpoint bind as 'interface:probe' "
            "(overrides the configuration file; empty disables the endpoint)."
        ),
    )
    parser.add_argument(
        "--live",
        dest="live",
        action="store_true",
        default=None,
        help="Enable live-capture workers in the headless service for this run.",
    )
    parser.add_argument(
        "--no-live",
        dest="live",
        action="store_false",
        default=None,
        help="Disable live-capture workers in the headless service for this run.",
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
    # NOTE (PHASE 16): `--serve` is the headless sync service (above). The
    # PHASE-17 web/API boundary keeps its own flag so the two never collide:
    # `--api-serve` starts the API, `--serve` starts the sync loop. The API
    # still runs "over the headless core" as its brief requires.
    parser.add_argument(
        "--api-serve",
        action="store_true",
        help="Run the web/API boundary (PHASE 17) over the headless core.",
    )
    parser.add_argument(
        "--api-host",
        default="127.0.0.1",
        help="Host interface for --api-serve (default 127.0.0.1; never expose without HTTPS).",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8080,
        help="TCP port for --api-serve (default 8080).",
    )
    return parser


def _launches_gui(args: argparse.Namespace) -> bool:
    """Whether this run ends in the GUI rather than a one-shot or service mode."""
    return not (args.write_config or args.serve or args.serve_once or args.headless)


def _claim_gui_instance(data_dir: Path | None) -> tuple[SingleInstance | None, bool]:
    """Claim the data folder for this GUI launch.

    Returns ``(instance, handed_off)``. ``handed_off`` means another copy
    already owns the folder and has been asked to show itself, so this
    process should exit without touching the database. ``instance`` is
    ``None`` when PySide6 is missing; the GUI branch reports that itself.
    """
    try:
        from clockmanager.gui.single_instance import SingleInstance
    except ImportError:
        return None, False
    resolved = data_dir.expanduser() if data_dir is not None else default_data_dir()
    instance = SingleInstance.for_data_dir(resolved)
    if instance.acquire():
        return instance, False
    instance.activate_running_instance()
    return None, True


def main(argv: list[str] | None = None) -> int:
    """Run the application. Returns a process exit code."""
    # Before argparse can print anything: --help and --version write straight
    # to stdout and exit, so this has to happen ahead of parse_args().
    make_console_output_safe(sys.stdout, sys.stderr)
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

    if args.api_serve:
        try:
            from clockmanager.api.server import run as run_api
        except ImportError as exc:
            print(
                "The web/API boundary requires FastAPI and uvicorn. Install them with "
                f"'pip install -e .[api]'. ({exc})",
                file=sys.stderr,
            )
            return EXIT_ERROR
        run_api(host=args.api_host, port=args.api_port)
        return EXIT_OK

    instance: SingleInstance | None = None
    if _launches_gui(args):
        instance, handed_off = _claim_gui_instance(args.data_dir)
        if handed_off:
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

        if args.interval is not None and args.interval < MIN_SERVICE_POLL_SECONDS:
            print(
                f"--interval must be at least {MIN_SERVICE_POLL_SECONDS} seconds.",
                file=sys.stderr,
            )
            return EXIT_ERROR

        if args.serve_once or args.serve:
            from clockmanager.headless.runner import HeadlessService

            service = HeadlessService(
                context,
                poll_seconds=args.interval,
                health_bind=args.health_bind,
                live_capture=args.live,
            )
            if args.serve_once:
                results = service.run_once()
                if not results:
                    print("No enabled devices were due for a sync.")
                for result in results:
                    print(f"{result.device_name}: {result.summary}")
                    if not result.ok:
                        print(f"  error: {result.error}", file=sys.stderr)
                return EXIT_OK
            stop_event = threading.Event()

            def _request_stop(signum: int, _frame: object) -> None:
                print(f"Received signal {signum}; shutting down.", file=sys.stderr)
                stop_event.set()

            try:
                signal.signal(signal.SIGINT, _request_stop)
                signal.signal(signal.SIGTERM, _request_stop)
            except (OSError, ValueError):  # pragma: no cover - platform without signals
                pass
            return service.run(stop_event)

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

        return run_gui(context, instance=instance)
    except ClockManagerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        context.shutdown()
        if instance is not None:
            instance.release()


if __name__ == "__main__":
    raise SystemExit(main())
