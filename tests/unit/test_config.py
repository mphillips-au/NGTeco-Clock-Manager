"""Configuration resolution tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clockmanager.config import (
    CONFIG_FILE_NAME,
    DATABASE_FILE_NAME,
    AppConfig,
    AppPaths,
    default_data_dir,
    load_config,
    save_config,
)
from clockmanager.errors import ConfigurationError


def test_defaults(tmp_path: Path) -> None:
    config = load_config(data_dir=tmp_path, environ={})
    assert config.log_level == "INFO"
    assert config.log_format == "json"
    assert config.developer_mode is False
    assert config.paths.data_dir == tmp_path
    assert config.paths.config_file.name == CONFIG_FILE_NAME
    assert config.paths.database_file.name == DATABASE_FILE_NAME


def test_database_url_points_at_data_dir(tmp_path: Path) -> None:
    config = load_config(data_dir=tmp_path, environ={})
    assert config.database_url.startswith("sqlite+pysqlite:///")
    assert DATABASE_FILE_NAME in config.database_url


def test_no_device_address_in_default_configuration(tmp_path: Path) -> None:
    """AGENTS.md: no hardcoded production IP, no secrets in source."""
    serialised = json.dumps(load_config(data_dir=tmp_path, environ={}).to_dict())
    for forbidden in ("host", "ip", "address", "port", "password", "pin", "secret"):
        assert forbidden not in serialised.lower()


def test_config_file_overrides_defaults(tmp_path: Path) -> None:
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / CONFIG_FILE_NAME).write_text(
        json.dumps({"log_level": "DEBUG", "developer_mode": True}), encoding="utf-8"
    )
    config = load_config(data_dir=tmp_path, environ={})
    assert config.log_level == "DEBUG"
    assert config.developer_mode is True


def test_environment_overrides_config_file(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / CONFIG_FILE_NAME).write_text(json.dumps({"log_level": "DEBUG"}), encoding="utf-8")
    config = load_config(data_dir=tmp_path, environ={"CLOCKMANAGER_LOG_LEVEL": "warning"})
    assert config.log_level == "WARNING"


def test_environment_data_dir_is_honoured(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    config = load_config(environ={"CLOCKMANAGER_DATA_DIR": str(target)})
    assert config.paths.data_dir == target


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("YES", True), ("0", False), ("off", False)],
)
def test_boolean_environment_coercion(tmp_path: Path, value: str, expected: bool) -> None:
    config = load_config(data_dir=tmp_path, environ={"CLOCKMANAGER_DEVELOPER_MODE": value})
    assert config.developer_mode is expected


def test_invalid_boolean_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_config(data_dir=tmp_path, environ={"CLOCKMANAGER_DEVELOPER_MODE": "maybe"})


def test_invalid_log_level_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_config(data_dir=tmp_path, environ={"CLOCKMANAGER_LOG_LEVEL": "chatty"})


def test_invalid_log_format_rejected() -> None:
    with pytest.raises(ConfigurationError):
        AppConfig(log_format="xml")


def test_unknown_config_key_rejected(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / CONFIG_FILE_NAME).write_text(json.dumps({"nope": 1}), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(data_dir=tmp_path, environ={})


def test_malformed_config_file_rejected(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / CONFIG_FILE_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(data_dir=tmp_path, environ={})


def test_save_config_round_trip(tmp_path: Path) -> None:
    original = AppConfig(paths=AppPaths(tmp_path), log_level="DEBUG", developer_mode=True)
    written = save_config(original)
    assert written.exists()

    reloaded = load_config(data_dir=tmp_path, environ={})
    assert reloaded.to_dict() == original.to_dict()


def test_ensure_creates_directories(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "appdata")
    paths.ensure()
    assert paths.data_dir.is_dir()
    assert paths.log_dir.is_dir()
    assert paths.backup_dir.is_dir()


def test_default_data_dir_is_user_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOCKMANAGER_DATA_DIR", raising=False)
    assert "NGTecoClockManager" in str(default_data_dir())
