"""GUI test fixtures.

Every GUI test runs offscreen against the mock device, so nothing here needs a
display or a real clock.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is not installed (pip install -e .[gui])")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from clockmanager.config import AppConfig, AppPaths
from clockmanager.gui.app import build_application
from clockmanager.services.application import ApplicationContext, bootstrap
from clockmanager.services.devices import DeviceProfile


@pytest.fixture(scope="session")
def qt_app() -> QApplication:
    return build_application([])


@pytest.fixture
def mock_context(tmp_path: Path) -> Iterator[ApplicationContext]:
    """An application context wired to the built-in mock device."""
    config = AppConfig(
        paths=AppPaths(tmp_path / "appdata"),
        log_to_console=False,
        use_mock_device=True,
    )
    context = bootstrap(config=config)
    try:
        yield context
    finally:
        context.shutdown()


@pytest.fixture
def configured_context(mock_context: ApplicationContext) -> ApplicationContext:
    """A mock context that already has one enabled device profile saved."""
    mock_context.devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))
    return mock_context


def drain(qt_app: QApplication, *, timeout_ms: int = 10_000) -> None:
    """Wait for background workers, then let queued signals be delivered."""
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    for _ in range(5):
        qt_app.processEvents()
