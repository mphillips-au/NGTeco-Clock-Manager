"""Diagnostics: structured logging now, device diagnostics from PHASE 02."""

from __future__ import annotations

from clockmanager.diagnostics.logging_setup import (
    RedactingFilter,
    configure_logging,
    get_logger,
)

__all__ = ["RedactingFilter", "configure_logging", "get_logger"]
