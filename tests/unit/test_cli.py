"""Entry-point tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from clockmanager.__main__ import EXIT_OK, main
from clockmanager.config import CONFIG_FILE_NAME


def test_headless_bootstrap_prints_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "appdata"
    assert main(["--headless", "--data-dir", str(data_dir)]) == EXIT_OK

    output = capsys.readouterr().out
    assert "Database file" in output
    assert "Database schema" in output
    assert (data_dir / "clockmanager.sqlite3").exists()


def test_write_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "appdata"
    assert main(["--write-config", "--data-dir", str(data_dir)]) == EXIT_OK
    assert (data_dir / CONFIG_FILE_NAME).exists()
    assert "Configuration written" in capsys.readouterr().out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "NGTeco Clock Manager" in capsys.readouterr().out


def test_headless_output_has_no_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--headless", "--data-dir", str(tmp_path / "appdata")])
    output = capsys.readouterr().out.lower()
    for forbidden in ("password", "pin ", "secret", "token"):
        assert forbidden not in output
