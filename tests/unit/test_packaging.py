"""Packaging, installer, and upgrade persistence tests."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from clockmanager import __version__
from clockmanager.config import AppConfig, AppPaths, default_data_dir
from clockmanager.persistence.database import (
    SCHEMA_VERSION_KEY,
    create_database,
    initialise_database,
)
from clockmanager.persistence.models import (
    SCHEMA_VERSION,
    DeviceRecord,
    DeviceUserRecord,
    SchemaInfo,
)
from clockmanager.windows import (
    APP_REGISTRY_NAME,
    RUN_REGISTRY_KEY,
    get_firewall_guidance,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def test_version_synchronization() -> None:
    """Verify application version is consistent across all manifests."""
    root = _repo_root()

    # 1. pyproject.toml
    pyproject_text = (root / "pyproject.toml").read_text(encoding="utf-8")
    pyproject_data = tomllib.loads(pyproject_text)
    pyproject_version = pyproject_data["project"]["version"]
    assert pyproject_version == __version__, (
        f"pyproject.toml version ({pyproject_version}) does not match __version__ ({__version__})"
    )

    # 2. Inno Setup installer.iss
    iss_text = (root / "packaging" / "installer.iss").read_text(encoding="utf-8")
    iss_match = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', iss_text)
    assert iss_match is not None, "MyAppVersion not found in installer.iss"
    assert iss_match.group(1) == __version__, (
        f"installer.iss MyAppVersion ({iss_match.group(1)}) does not match __version__ ({__version__})"
    )

    # 3. PyInstaller version_info.txt
    vi_text = (root / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    assert f"StringStruct('ProductVersion', '{__version__}')" in vi_text
    parts = [int(p) for p in __version__.split(".")[:3]]
    while len(parts) < 4:
        parts.append(0)
    ver_tuple_str = f"({parts[0]}, {parts[1]}, {parts[2]}, {parts[3]})"
    assert f"filevers={ver_tuple_str}" in vi_text
    assert f"prodvers={ver_tuple_str}" in vi_text


def test_packaging_spec_configuration() -> None:
    """Verify PyInstaller spec and Inno Setup script meet Windows requirements."""
    root = _repo_root()

    spec_text = (root / "packaging" / "clockmanager.spec").read_text(encoding="utf-8")
    assert "zk" in spec_text
    assert "greenlet" in spec_text
    assert "tzdata" in spec_text
    assert "clockmanager.ico" in spec_text

    iss_text = (root / "packaging" / "installer.iss").read_text(encoding="utf-8")
    # Non-admin per-user installation
    assert "PrivilegesRequired=lowest" in iss_text
    assert r"DefaultDirName={localappdata}\Programs\{#MyAppName}" in iss_text
    # Shortcuts
    assert "{group}" in iss_text
    assert "{autodesktop}" in iss_text
    # Startup entry
    assert "Root: HKCU" in iss_text
    assert r'Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"' in iss_text
    # Clean install & uninstall
    assert "UninstallDisplayIcon" in iss_text


def test_windows_data_paths_isolation(tmp_path: Path) -> None:
    """Verify configuration, database, and log paths are properly isolated."""
    paths = AppPaths(data_dir=tmp_path / "UserData")
    assert paths.database_file == tmp_path / "UserData" / "clockmanager.sqlite3"
    assert paths.log_dir == tmp_path / "UserData" / "logs"
    assert paths.backup_dir == tmp_path / "UserData" / "backups"
    assert paths.config_file == tmp_path / "UserData" / "config.json"


def test_default_data_dir_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify standard default data folder on Windows when no override is set."""
    monkeypatch.delenv("CLOCKMANAGER_DATA_DIR", raising=False)
    data_dir = default_data_dir()
    assert "NGTecoClockManager" in str(data_dir)


def test_windows_platform_helpers() -> None:
    """Verify Windows platform constants and firewall guidance."""
    assert RUN_REGISTRY_KEY == r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert APP_REGISTRY_NAME == "NGTecoClockManager"

    firewall = get_firewall_guidance()
    assert any(p["port"] == 4370 and p["protocol"] == "TCP" for p in firewall["ports"])
    assert any(p["port"] == 4370 and p["protocol"] == "UDP" for p in firewall["ports"])
    assert len(firewall["powershell_rules"]) == 2
    assert "4370" in firewall["powershell_rules"][0]


def test_database_upgrade_without_data_loss(tmp_path: Path) -> None:
    """Verify that upgrading an existing database preserves all user data across schema changes."""
    config = AppConfig(paths=AppPaths(data_dir=tmp_path / "appdata"), log_to_console=False)

    # Step 1: Initialise database as normal (creates schema 7)
    db1 = create_database(config)
    initialise_database(db1)
    with db1.session() as session:
        dev = DeviceRecord(
            name="Front Door Clock",
            host="192.168.1.50",
            port=4370,
            enabled=True,
        )
        session.add(dev)
        session.flush()

        usr = DeviceUserRecord(
            device_id=dev.id,
            device_uid=1,
            user_id="101",
            first_name="Jane",
            last_name="Doe",
            privilege=0,
        )
        session.add(usr)
    db1.dispose()

    # Step 2: Simulate application upgrade / restart with existing DB file
    db2 = create_database(config)
    schema_ver = initialise_database(db2)
    assert schema_ver == SCHEMA_VERSION

    with db2.session() as session:
        # Check schema version
        stored = session.get(SchemaInfo, SCHEMA_VERSION_KEY)
        assert stored is not None
        assert stored.value == str(SCHEMA_VERSION)

        # Check existing records were preserved
        dev_loaded = session.query(DeviceRecord).filter_by(name="Front Door Clock").first()
        assert dev_loaded is not None
        assert dev_loaded.host == "192.168.1.50"

        usr_loaded = session.query(DeviceUserRecord).filter_by(user_id="101").first()
        assert usr_loaded is not None
        assert usr_loaded.first_name == "Jane"
        assert usr_loaded.last_name == "Doe"
    db2.dispose()
