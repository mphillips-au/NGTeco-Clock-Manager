"""Shared pytest fixtures.

Every test runs against a temporary data directory. No test may touch the real
user profile, and no test may require a real NG-MB1.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from clockmanager.config import AppConfig, AppPaths
from clockmanager.services.application import ApplicationContext, bootstrap


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every test away from the real application data directory."""
    monkeypatch.setenv("CLOCKMANAGER_DATA_DIR", str(tmp_path / "appdata"))


@pytest.fixture(autouse=True)
def _reset_application_logger() -> Iterator[None]:
    """Remove handlers installed by a test so log files can be cleaned up."""
    yield
    logger = logging.getLogger("clockmanager")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    """A configuration rooted in a temporary directory, without console noise."""
    return AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)


@pytest.fixture
def context(app_config: AppConfig) -> Iterator[ApplicationContext]:
    """A fully bootstrapped application context backed by a temporary SQLite file."""
    ctx = bootstrap(config=app_config)
    try:
        yield ctx
    finally:
        ctx.shutdown()
