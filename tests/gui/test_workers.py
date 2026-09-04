"""Background worker tests.

The worker is how the GUI keeps I/O off the UI thread (``AGENTS.md``), so both
its success and failure paths are covered.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.gui

pytest.importorskip("PySide6", reason="PySide6 is not installed (pip install -e .[gui])")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from clockmanager.gui.app import build_application  # noqa: E402
from clockmanager.gui.workers import CallableWorker  # noqa: E402


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return build_application([])


def _run(worker: CallableWorker, qt_app: QApplication) -> None:
    QThreadPool.globalInstance().start(worker)
    QThreadPool.globalInstance().waitForDone(10_000)
    qt_app.processEvents()


def test_worker_emits_result(qt_app: QApplication) -> None:
    results: list[object] = []
    worker = CallableWorker(lambda: 42)
    worker.signals.finished.connect(results.append)
    _run(worker, qt_app)
    assert results == [42]


def test_worker_reports_failure_without_raising(qt_app: QApplication) -> None:
    failures: list[str] = []

    def boom() -> None:
        raise RuntimeError("device unreachable")

    worker = CallableWorker(boom)
    worker.signals.failed.connect(failures.append)
    _run(worker, qt_app)
    assert failures == ["device unreachable"]
