"""Logging tests: structured output, and redaction on every handler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clockmanager.config import AppConfig, AppPaths
from clockmanager.diagnostics.logging_setup import (
    LOG_FILE_NAME,
    configure_logging,
    get_logger,
)
from clockmanager.security.redaction import REDACTED


def _config(tmp_path: Path, **overrides: object) -> AppConfig:
    return AppConfig(paths=AppPaths(tmp_path), log_to_console=False, **overrides)  # type: ignore[arg-type]


def test_configure_logging_creates_log_file(tmp_path: Path) -> None:
    log_file = configure_logging(_config(tmp_path))
    assert log_file.name == LOG_FILE_NAME
    assert log_file.parent.exists()

    get_logger("clockmanager.test").info("hello")
    assert log_file.read_text(encoding="utf-8").strip()


def test_json_format_is_one_object_per_line(tmp_path: Path) -> None:
    log_file = configure_logging(_config(tmp_path))
    get_logger("clockmanager.test").info("bootstrap complete", extra={"schema_version": 1})

    lines = [line for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    payload = json.loads(lines[-1])
    assert payload["message"] == "bootstrap complete"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "clockmanager.test"
    assert payload["schema_version"] == 1


def test_sensitive_extra_fields_are_redacted(tmp_path: Path) -> None:
    log_file = configure_logging(_config(tmp_path))
    get_logger("clockmanager.test").info(
        "writing user", extra={"user_id": "1001", "pin": "4821", "card_number": "0012345678"}
    )

    text = log_file.read_text(encoding="utf-8")
    assert "4821" not in text
    assert "0012345678" not in text
    assert REDACTED in text
    assert "1001" in text


def test_sensitive_message_content_is_redacted(tmp_path: Path) -> None:
    log_file = configure_logging(_config(tmp_path))
    get_logger("clockmanager.test").warning("auth failed with password=hunter2")
    assert "hunter2" not in log_file.read_text(encoding="utf-8")


def test_byte_payloads_are_not_written(tmp_path: Path) -> None:
    log_file = configure_logging(_config(tmp_path))
    get_logger("clockmanager.test").debug("packet %s", b"\x01\x02secret")
    assert "secret" not in log_file.read_text(encoding="utf-8")


def test_text_format_option(tmp_path: Path) -> None:
    log_file = configure_logging(_config(tmp_path, log_format="text"))
    get_logger("clockmanager.test").info("plain message")
    line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "plain message" in line
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_reconfiguring_does_not_duplicate_handlers(tmp_path: Path) -> None:
    configure_logging(_config(tmp_path))
    log_file = configure_logging(_config(tmp_path))
    get_logger("clockmanager.test").info("only once")

    lines = [
        line for line in log_file.read_text(encoding="utf-8").splitlines() if "only once" in line
    ]
    assert len(lines) == 1


def test_get_logger_namespaces_plain_names() -> None:
    assert get_logger("widgets").name == "clockmanager.widgets"
    assert get_logger("clockmanager.widgets").name == "clockmanager.widgets"
