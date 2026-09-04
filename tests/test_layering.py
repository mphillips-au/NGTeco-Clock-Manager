"""Architectural guard rails.

ARCHITECTURE.md: the reusable core must not import PySide6, so the same code
can run as a headless Linux/Synology service.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "clockmanager"

CORE_PACKAGES = ["domain", "persistence", "protocol", "sync", "services", "security", "diagnostics"]


def _core_modules() -> list[Path]:
    modules = [SRC / "__init__.py", SRC / "config.py", SRC / "errors.py", SRC / "__main__.py"]
    for package in CORE_PACKAGES:
        modules.extend(sorted((SRC / package).rglob("*.py")))
    return modules


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module", _core_modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_core_modules_do_not_import_pyside6(module: Path) -> None:
    offending = {name for name in _imported_names(module) if name.split(".")[0] == "PySide6"}
    assert not offending, f"{module} imports {sorted(offending)}"


def test_core_bootstrap_runs_without_pyside6_loaded() -> None:
    """Importing and bootstrapping the core must not pull PySide6 into memory."""
    script = (
        "import sys, tempfile;"
        "from pathlib import Path;"
        "from clockmanager.config import AppConfig, AppPaths;"
        "from clockmanager.services.application import bootstrap;"
        "d = tempfile.mkdtemp();"
        "cfg = AppConfig(paths=AppPaths(Path(d)), log_to_console=False);"
        "ctx = bootstrap(config=cfg);"
        "ctx.status();"
        "ctx.shutdown();"
        "print('PySide6' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().endswith("False")


def test_gui_layer_does_not_import_persistence_or_protocol_directly() -> None:
    """The GUI must go through application services."""
    for module in sorted((SRC / "gui").rglob("*.py")):
        names = _imported_names(module)
        forbidden = {
            name
            for name in names
            if name.startswith(("clockmanager.persistence", "clockmanager.protocol"))
        }
        assert not forbidden, f"{module} imports {sorted(forbidden)}"
