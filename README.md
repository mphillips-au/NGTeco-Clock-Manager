# NGTeco Clock Manager

Windows-first attendance management software for NGTeco NG-MB1 devices.

## Start here

1. Read `AGENTS.md`.
2. Read `PLAN.md`.
3. Read `STATUS.md`.
4. Start with the current phase in `phases/`.
5. Use one phase per coding session.
6. Update `STATUS.md` and `CHANGELOG.md` after each session.

## Important

The NG-MB1 uses a verified 120-byte user record and should not be treated as a
generic pyzk 72-byte device.

## Development setup

Python 3.12 or newer is required.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[gui,dev]"
```

Omit the `gui` extra for a headless install; the core runs without PySide6.

## Running

```bash
.venv/Scripts/clockmanager.exe
```

Useful flags:

- `--headless` — bootstrap configuration, logging and the database, print the
  status table, and exit without importing PySide6.
- `--data-dir PATH` — use an alternative data directory instead of the per-user
  one. Always use this when experimenting.
- `--write-config` — write the resolved configuration to `config.json`.

### Running without a device

Set `CLOCKMANAGER_USE_MOCK_DEVICE=1` to run the application against the built-in
mock device. Every view works, backed by synthetic data, and the status bar says
so. Useful for development and demonstrations with no clock attached.

## Checks

```bash
.venv/Scripts/python.exe -m pytest
```

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
```

```bash
.venv/Scripts/python.exe -m mypy
```

## Application data

Configuration, the SQLite database, logs and backups live in one per-user
directory:

- Windows: `%LOCALAPPDATA%\NGTecoClockManager`
- Linux: `$XDG_DATA_HOME/NGTecoClockManager` (or `~/.local/share/...`)

Environment overrides use the `CLOCKMANAGER_` prefix, for example
`CLOCKMANAGER_DATA_DIR`, `CLOCKMANAGER_LOG_LEVEL`,
`CLOCKMANAGER_DEVELOPER_MODE` and `CLOCKMANAGER_USE_MOCK_DEVICE`.

No device address and no credential is stored in source or in the default
configuration. Device connection settings are configured in the application and
stored in the local database.

## Real-device tests

The integration suite never runs by default. To run it against a real clock:

```bash
CLOCKMANAGER_TEST_DEVICE_HOST=<your-device-ip> .venv/Scripts/python.exe -m pytest tests/integration -m real_device
```

Those tests are read-only. This build never writes to a device.
