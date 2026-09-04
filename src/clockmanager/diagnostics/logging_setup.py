"""Structured logging with mandatory redaction.

Every handler installed here passes records through :class:`RedactingFilter`,
so a caller cannot accidentally emit a PIN, card identifier or biometric
template by attaching it to a log record (``SECURITY.md``).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path
from typing import Any, Final

from clockmanager import __version__
from clockmanager.config import AppConfig
from clockmanager.security.redaction import (
    REDACTED,
    is_sensitive_key,
    redact_bytes,
    redact_text,
)

__all__ = [
    "LOG_FILE_NAME",
    "JsonFormatter",
    "RedactingFilter",
    "configure_logging",
    "get_logger",
]

LOG_FILE_NAME: Final = "clockmanager.log"
_ROOT_LOGGER_NAME: Final = "clockmanager"
_MAX_LOG_BYTES: Final = 2 * 1024 * 1024
_LOG_BACKUP_COUNT: Final = 5

#: Attributes present on every ``LogRecord``; anything else is caller context.
_STANDARD_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


class RedactingFilter(logging.Filter):
    """Redact sensitive content from a log record before it is formatted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            record.args = self._redact_args(record.args)
        for key, value in list(record.__dict__.items()):
            if key in _STANDARD_RECORD_FIELDS:
                continue
            record.__dict__[key] = REDACTED if is_sensitive_key(key) else _redact(value)
        return True

    @staticmethod
    def _redact_args(args: Any) -> Any:
        if isinstance(args, dict):
            return {
                key: (REDACTED if is_sensitive_key(str(key)) else _redact(value))
                for key, value in args.items()
            }
        if isinstance(args, tuple):
            return tuple(_redact(value) for value in args)
        return _redact(args)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes | bytearray):
        return redact_bytes(bytes(value))
    if isinstance(value, dict):
        return {
            key: (REDACTED if is_sensitive_key(str(key)) else _redact(inner))
            for key, inner in value.items()
        }
    return value


class JsonFormatter(logging.Formatter):
    """Render log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "app_version": __version__,
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _text_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def configure_logging(config: AppConfig, *, log_dir: Path | None = None) -> Path:
    """Install the application log handlers and return the log file path.

    Calling this repeatedly is safe: existing handlers on the application
    logger are removed first.
    """
    target_dir = log_dir if log_dir is not None else config.paths.log_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    log_file = target_dir / LOG_FILE_NAME

    formatter: logging.Formatter = (
        JsonFormatter() if config.log_format == "json" else _text_formatter()
    )
    redactor = RedactingFilter()

    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(config.log_level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)
    logger.addHandler(file_handler)

    if config.log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.addFilter(redactor)
        logger.addHandler(console_handler)

    return log_file


def get_logger(name: str) -> logging.Logger:
    """Return a logger inside the application logging tree.

    ``name`` is normally ``__name__``; module names already start with
    ``clockmanager.`` so they attach to the configured handlers directly.
    """
    if name == _ROOT_LOGGER_NAME or name.startswith(f"{_ROOT_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")
