"""Application bootstrap tests."""

from __future__ import annotations

from pathlib import Path

from clockmanager import APPLICATION_NAME, __version__
from clockmanager.config import AppConfig, AppPaths
from clockmanager.persistence.models import SCHEMA_VERSION
from clockmanager.services.application import ApplicationContext, bootstrap


def test_bootstrap_creates_data_dir_database_and_log(tmp_path: Path) -> None:
    config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
    with bootstrap(config=config) as context:
        assert config.paths.data_dir.is_dir()
        assert config.paths.database_file.exists()
        assert context.log_file.exists()
        assert context.schema_version == SCHEMA_VERSION


def test_status_snapshot(context: ApplicationContext) -> None:
    status = context.status()
    assert status.application_name == APPLICATION_NAME
    assert status.version == __version__
    assert status.schema_version == SCHEMA_VERSION
    assert status.known_devices == 0
    assert status.developer_mode is False

    labels = [label for label, _ in status.as_rows()]
    assert "Database file" in labels
    assert "Known devices" in labels


def test_status_contains_no_sensitive_fields(context: ApplicationContext) -> None:
    rendered = str(context.status().as_rows()).lower()
    for forbidden in ("pin", "password", "secret", "token", "card number"):
        assert forbidden not in rendered


def test_schema_info_exposed_for_diagnostics(context: ApplicationContext) -> None:
    assert context.schema_info()["schema_version"] == str(SCHEMA_VERSION)


def test_bootstrap_is_repeatable_on_existing_data_dir(tmp_path: Path) -> None:
    config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
    with bootstrap(config=config):
        pass
    with bootstrap(config=config) as second:
        assert second.schema_version == SCHEMA_VERSION


def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
    context = bootstrap(config=config)
    context.shutdown()
    context.shutdown()
