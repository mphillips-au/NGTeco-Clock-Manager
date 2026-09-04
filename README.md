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
`CLOCKMANAGER_DEVELOPER_MODE`, `CLOCKMANAGER_USE_MOCK_DEVICE`,
`CLOCKMANAGER_ENABLE_DEVICE_WRITES` and
`CLOCKMANAGER_ENABLE_CREDENTIAL_WRITES`.

No device address and no credential is stored in source or in the default
configuration. Device connection settings are configured in the application and
stored in the local database.

## Device writing

User management (add, edit, delete) is **switched off by default**, and the
application reads from the clock without changing it.

The reason is not caution for its own sake. The MB1 write path is built on the
verified 120-byte record and is covered by unit tests, but **no NG-MB1 has yet
accepted a record from it**. Until one has, enabling it is a deliberate act:

```bash
CLOCKMANAGER_ENABLE_DEVICE_WRITES=1 .venv/Scripts/clockmanager.exe
```

Setting a PIN needs a second switch, because the layout of the record's
credential region is inferred rather than verified:

```bash
CLOCKMANAGER_ENABLE_CREDENTIAL_WRITES=1
```

With writing on, the application still refuses to guess: it never calls
`pyzk.set_user()`, it builds an exact 120-byte record, and every write is read
back from the device and compared before it is reported as done. Deleting a
user shows the exact record and its attendance impact and requires a
confirmation. Every attempt — succeeded, failed or refused — is recorded in the
Audit log view.

Prove it with a disposable test user before trusting it with real staff.

## Real-device tests

The integration suite never runs by default. To run the read-only tests against
a real clock:

```bash
CLOCKMANAGER_TEST_DEVICE_HOST=<your-device-ip> .venv/Scripts/python.exe -m pytest tests/integration -m real_device
```

The write tests need a second, separate opt-in. They create, modify and delete
only accounts prefixed `ZZTEST-`, and clean up after themselves:

```bash
CLOCKMANAGER_TEST_DEVICE_HOST=<ip> CLOCKMANAGER_TEST_ALLOW_WRITES=1 .venv/Scripts/python.exe -m pytest tests/integration -m real_device
```

Running that suite successfully is what turns the write path from unverified
into verified. Nothing in the suite clears attendance, resets the device or
touches a biometric template.
