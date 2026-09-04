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
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the application. Returns a process exit code."""
    args = _build_parser().parse_args(argv)

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
