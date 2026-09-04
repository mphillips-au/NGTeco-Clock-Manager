# CHANGELOG.md

## Unreleased

### PHASE 03 — MB1 User Management (2026-09-04)

Added user create/update/delete on the NG-MB1, built on the verified 120-byte
record. The path is implemented and tested but **unverified on hardware**, so it
ships switched off.

#### The 120-byte write path
- New `clockmanager.protocol.builders`: builds one exact 120-byte MB1 user
  record, validates every field before packing, and length-checks the result.
  `pyzk.set_user()` builds a 72-byte packet and is never called — a test asserts
  that against the module's parsed syntax tree rather than its text.
- `RawUserRecord` keeps a whole 120-byte record inside the protocol layer so the
  adapter can read-modify-write. Its `raw` is excluded from `repr`, and the type
  never reaches a service or the GUI.
- The credential region (bytes 3:35) is treated as opaque: an update copies it
  byte-for-byte, so renaming a user cannot destroy their PIN. Setting one writes
  only bytes 3:11, a candidate offset inferred from the generic ZKTeco record,
  and preserves the rest.
- `NGTecoMB1Device.apply_user_write()` and `.delete_user()` perform the whole
  sequence internally — read, validate, build, send, require an
  acknowledgement, read back, compare — so a caller cannot do it partially.
  Comparison is byte-exact outside the credential region and on presence within
  it. Writes are never retried.
- `CMD_REFRESHDATA` is sent after every write; a refusal there is reported as
  leaving the device in an unknown state, not as a success.

#### Honest capabilities
- New `Support.OPERATOR_ENABLED` and `DeviceCapabilities.unlocked()`. Writing is
  `UNVERIFIED` and unusable until an operator unlocks it, at which point it
  reports "operator-enabled (unverified)" — never `SUPPORTED`. An `UNSUPPORTED`
  capability cannot be unlocked at all.
- `WRITE_USERS` moved from `UNSUPPORTED` to `UNVERIFIED`: a real 120-byte path
  now exists. `WRITE_USER_PASSWORD` added as `UNVERIFIED` with its own unlock.
  `WRITE_USER_CARD` added as `UNSUPPORTED`; no card field has been identified.
- Two configuration switches, both off by default:
  `CLOCKMANAGER_ENABLE_DEVICE_WRITES` and
  `CLOCKMANAGER_ENABLE_CREDENTIAL_WRITES`. The second is ineffective without the
  first.

#### Domain and services
- New `clockmanager.domain.users`: `UserDraft` (validated intent, with
  `password` excluded from `repr`), `CredentialAction`, `UserChange`,
  `UserWriteOutcome`, `describe_changes`. Field limits are checked in **bytes**,
  so a form that accepts an accented name cannot produce a record the device
  rejects.
- New `UserService`: refuses a write the operator has not enabled and says why,
  validates before opening a connection, describes a delete's impact from a live
  read, and audits every attempt. `delete_user` takes `confirmed` as a required
  argument so a user cannot be deleted by forgetting to ask.
- `DeviceService.build()` hands out devices with write unlocks opt-in and
  keyword-only; a read can never receive a device that is allowed to write.

#### Audit log
- Schema version 3 adds the append-only `audit_events` table.
  `AuditRepository` offers `add`, `recent` and `count` and no way to edit or
  delete a row.
- Entries record refusals and failures as well as successes. `device_id` is a
  plain value rather than a foreign key, so removing a device cannot erase the
  record of what was done to it.
- Every `detail` is redacted before storage and truncated to the column width. A
  PIN change is recorded as an action, never as a value.

#### GUI
- Users view: search, admins-only filter, add/edit/delete, and a per-change
  confirmation that lists exactly what will be written. Buttons are disabled
  with an explanation when writing is off. Selection maps through the UID column
  rather than the row index, so sorting the table cannot target the wrong user.
- New user form dialog: offers only the two device-verified privileges,
  validates through the domain rules before it will close, masks the PIN field,
  never populates it from the device, and disables it unless PIN writing is
  unlocked.
- Deleting shows the user read live from the device plus its attendance count,
  and warns that attendance history is not removed.
- New Audit log view: read-only, filterable, with no clear action.
- The About box and status bar now state plainly whether this installation can
  change a device.

#### Mock device
- The mock gained the same write path and the same capability gates, so a test
  written against it cannot pass for code a real device would refuse.
- New `MockDeviceFactory` keeps one device per profile for the life of an
  application context, so writes persist across service calls and the write path
  can be exercised end to end with no hardware. State is per-instance, so two
  contexts never share devices.

#### Fixes
- `AuditView` no longer reads in its constructor, matching every other view. The
  previous behaviour let a pooled worker outlive the widget and crash Qt.
- A log call used `created` in `extra`, which collides with a reserved
  `LogRecord` attribute and raised whenever logging was configured.

#### Tests
- 498 tests, 91% statement coverage. New suites cover the record builder, the
  adapter write sequence against a recording fake transport, the mock's writes,
  the user service's policy and auditing, the audit log, schema version 3, and
  the GUI form, buttons and confirmations.
- New opt-in real-device write suite requiring a **second** switch
  (`CLOCKMANAGER_TEST_ALLOW_WRITES=1`) and a third for credentials. It touches
  only `ZZTEST-`-prefixed disposable accounts, cleans up after itself, and
  carries a syntax-tree guard against a future edit making it destructive.
- Existing PHASE 01/02 tests asserting "no write path exists" were updated to
  assert the PHASE 03 reality instead: writes exist but are gated, and
  attendance clearing, factory reset and biometric writing still do not exist.

#### Known limitations recorded
- No MB1 has accepted a record from this write path. Until the opt-in suite is
  run against hardware, treat it as unproven.
- The PIN offset inside the credential region is inferred, not verified.
- Whether the MB1 accepts an application-chosen UID for a new user is unverified.

### PHASE 02 — Windows GUI / Settings / Diagnostics (2026-09-04)

Turned the shell into a usable read-only application. No device writes.

#### Schema migrations
- Added a forward-only migration runner. Migrations apply one at a time, each
  recorded in `schema_info` before the next begins, so an interrupted upgrade
  resumes instead of half-applying. `ALTER TABLE` steps tolerate a column that
  already exists.
- Schema version 2 adds device connection settings to `devices`: host, port,
  communication password, timeout, auto reconnect, sync interval, enabled.
  Existing rows keep their data and take protocol defaults; `host` stays NULL
  because no address is ever invented.
- A database with tables but no recorded version is refused rather than guessed.

#### Application services
- `DeviceProfile` (the PHASE 02 settings), `DeviceService` (stored profiles plus
  read-only device operations) and `ConnectionTestResult`.
- `test_connection()` returns a failure as a result rather than raising, so the
  GUI can report a dead device without exception handling in a widget.
- Reads always disconnect, including when the device fails mid-read.
- `build_mock_device` plus synthetic sample data, so the whole application runs
  with no hardware attached.

#### GUI
- Navigation shell: a list on the left, a stacked view area, File/View/Help
  menus and a developer-mode-gated Developer menu.
- Dashboard — active device, its reported identity and application status.
- Users — device user list with privilege labels and a filter.
- Attendance — device attendance with direction from `punch`, raw `status`
  shown verbatim, and a filter.
- Live events — start/stop capture with a running list, newest first.
- Device settings — every field the phase specifies: name, IP, port,
  communication password, timeout, auto reconnect, sync interval, enabled.
- Diagnostics — test connection, device info, device time, user count,
  attendance count, capabilities, live capture state and the redacted log tail.

#### Threading
- `LiveCaptureWorker`, a `QThread` that opens, uses and closes the device
  connection entirely off the UI thread.
- `run_off_thread` routes every one-shot device and database call through the
  thread pool. No view performs I/O in its constructor.

#### Security
- The communication password is excluded from `repr`, masked in the GUI, never
  echoed back into the form once saved, and never logged. Verified: after
  saving a password, it does not appear anywhere in the log file.
- Recorded in `SECURITY.md` that the password is stored **unencrypted at rest**,
  with the data directory's OS permissions as the only control. Encryption is an
  open item.
- The user list shows only whether a credential region is populated, never its
  contents.
- Diagnostic reason text is gated behind `developer_mode`.

#### Tests
- 364 tests, 6 opt-in real-device tests deselected.
- Migration tests including a real v1 database with rows, upgraded in place with
  data preserved, defaults applied, idempotent re-runs and a resumed partial
  upgrade.
- Service tests for profile storage, validation, connection testing and
  disconnect-on-failure.
- GUI tests against the mock device for every view.
- Thread guards that record which thread each device call actually ran on and
  assert it was not the UI thread.

#### Fixed
- `build_table` now takes `sortable`. Label/value tables had sorting enabled, so
  the dashboard and diagnostics panels reordered their rows alphabetically and
  scrambled a meaningful order. Caught by inspecting a screenshot.
- The device settings view imported `protocol.constants` directly, breaking the
  GUI-to-services boundary. Caught by the architecture test; the default port is
  now re-exported from the services layer.

### PHASE 01 — MB1 Read-Only Protocol Core (2026-09-04)

Built the reusable NG-MB1 device layer. Read-only: no write, delete, clear or
reset operation exists, and no code in this phase has contacted a real device.

#### Device layer
- `AttendanceDevice` protocol interface and `DeviceConnectionSettings`
  (name, host, port, timeout, communication password, encoding). No default
  host; the communication password is excluded from `repr`.
- `NGTecoMB1Device` with connect/disconnect, context-manager support, device
  info, device time, users, attendance and live capture.
- `MockAttendanceDevice` and `MockDeviceScript` in the shipped package, so the
  GUI and tests can run with no hardware. Uses the RFC 5737 documentation
  address range.
- `RetryPolicy` and `call_with_retry` with exponential backoff, capped delay and
  an optional reconnect hook. Only connection faults are retried; parse and
  capability errors fail immediately. The concrete error type survives
  exhaustion, so a timeout is still distinguishable from a dropped link.
- Structured exceptions under `DeviceError`: connection, timeout,
  authentication, protocol, parse, not-connected and capability.

#### Capability model
- `Capability`/`Support`/`DeviceCapabilities`, with every capability carrying
  the evidence for its state. `require()` refuses anything not verified.
- NG-MB1: connect, device info, time, users, attendance and live capture are
  SUPPORTED. `write_users` and `clear_attendance` are UNSUPPORTED; `set_time`,
  `delete_users`, `read_fingerprint` and `read_face` are UNVERIFIED.

#### MB1 parsing (application-owned)
- 120-byte user record parser for the verified layout: 0:2 UID, 2 privilege,
  3:35 credential region, 35:59 first name, 59:96 last name, 96:120 user ID.
- The credential region is inspected for presence only; its contents are never
  decoded, returned or logged.
- Attendance parser for the 8-, 16- and 40-byte forms. The device's record count
  is authoritative for record size; an ambiguous payload is refused.
- Live-event parser for the 12-, 32-, 36- and 52-byte forms.
- ZKTeco packed and per-field timestamp decoding. Both produce naive
  device-local time, which the device transmits without a timezone.
- Unrecognised layouts raise `DeviceParseError` rather than being guessed at.

#### Protocol findings (recorded in `PROTOCOL.md`)
- `pyzk.get_users()` mis-parses 120-byte MB1 records in 72-byte strides.
- `pyzk.get_attendance()` and `pyzk.live_capture()` both depend on it
  internally, so neither is usable as-is; both paths are owned here.
- `pyzk.live_capture()` evaluates `int(user_id)` for unknown users and raises on
  a non-numeric MB1 user ID. A regression test pins that this case works.
- pyzk pinned to `==0.9`: the live loop uses its name-mangled socket surface,
  isolated in one accessor that fails clearly if pyzk changes.

#### Domain
- Added `DeviceInfo` (identity, device clock, record counts). Counts report
  `None` when the device did not supply them rather than implying zero.
- Added `DeviceUser.has_credential_data`, an indicator only; the inference that
  it always means "a PIN is set" is documented as unverified.

#### Tests
- 274 tests, 6 opt-in real-device tests deselected by default.
- Sanitized MB1 fixture builders under `tests/fixtures/`, with a non-secret
  marker byte filling the credential region.
- Parser tests covering field offsets, endianness, privileges, non-ASCII and
  maximum-length names, empty user IDs, and rejection of the generic 28/72-byte
  shapes.
- Adapter tests using a fake transport: error translation, retry/reconnect,
  capability enforcement, and a guard that no write method exists.
- Live-capture tests including the non-numeric user ID that breaks pyzk.
- Opt-in real-device suite, gated on `CLOCKMANAGER_TEST_DEVICE_HOST` and the
  `real_device` marker, read-only, with a self-guard against gaining writes.

#### Tooling
- Added the `DTZ` (flake8-datetimez) ruleset so naive datetimes must be
  deliberate rather than accidental.

### PHASE 00 — Repository Bootstrap (2026-08-29)

Added the software foundation. No device communication and no device writes.

#### Project
- `pyproject.toml` with a `src/` layout, `clockmanager` console entry point,
  optional `gui` (PySide6) and `dev` (pytest/ruff/mypy) extras.
- ruff (lint + format) and mypy in strict mode configured for Python 3.12.
- `.gitignore` covering databases, logs, local data, protocol captures and
  secrets.

#### Package structure
- Separated `domain`, `persistence`, `protocol`, `sync`, `services`,
  `security`, `diagnostics` and `gui`.
- `protocol` and `sync` are documented boundaries only; they are implemented in
  PHASE 01 and PHASE 04.
- A ruff banned-import rule and an AST-based test both enforce that only
  `clockmanager.gui` imports PySide6.

#### Domain
- `Privilege` (0 Employee, 14 Admin) and `PunchDirection` (0 IN, 1 OUT),
  encoding only values verified on the real NG-MB1.
- Unknown privilege/punch values are preserved and reported as
  `Unknown (n)` rather than guessed.
- `DeviceIdentity`, `DeviceUser` and `AttendanceEvent`. `AttendanceEvent`
  preserves the raw `status` field and derives direction from `punch` only.
- `DeviceUser` deliberately carries no credential/PIN field.

#### Persistence
- SQLAlchemy 2.0 ORM over SQLite, schema version 1: `schema_info`, `devices`,
  `device_users`, `attendance_events`.
- Multi-device design from the start: users and attendance events are attributed
  to a source device.
- Attendance natural key (`device_id`, `user_id`, `occurred_at`, `punch`,
  `status`) reserved for PHASE 04 duplicate detection.
- SQLite `foreign_keys` and WAL pragmas enabled per connection.
- `PersistenceError` wraps SQLAlchemy failures; sessions roll back on error.
- A database written by a newer schema version is refused, not downgraded.

#### Configuration
- Resolution order: defaults, then the JSON config file, then `CLOCKMANAGER_*`
  environment variables; unknown keys are rejected.
- Per-user data directory (`%LOCALAPPDATA%\NGTecoClockManager` on Windows,
  `$XDG_DATA_HOME` on Linux), overridable with `--data-dir` or
  `CLOCKMANAGER_DATA_DIR`.
- No device address, port or credential is stored or defaulted in source.

#### Logging and security
- Structured JSON logging (rotating file plus optional console) with a
  `RedactingFilter` installed on every handler.
- Redaction covers sensitive record attributes, formatted `key=value` text in
  messages, and byte payloads (reported as a length, never as content).
- `developer_mode` gates diagnostic views; the GUI Developer menu is hidden
  unless it is enabled.

#### Services and entry point
- `bootstrap()` builds configuration, logging and the database and returns an
  `ApplicationContext`; `ApplicationStatus` is a display-safe snapshot.
- `clockmanager` starts the GUI; `clockmanager --headless` performs the same
  bootstrap without importing PySide6, which is the path the future
  Linux/Synology service will use. `clockmanager --write-config` writes the
  resolved configuration.

#### GUI
- PySide6 main window shell showing application status, with File/Help menus
  and a developer-mode-gated Developer menu.
- `CallableWorker` runs work on the Qt thread pool so GUI I/O never blocks the
  UI thread, including the status load in this build.

#### Tests
- 136 tests: domain, redaction, logging, configuration, database, services,
  CLI, architectural layering and GUI smoke tests. 91% statement coverage.
- The layering test spawns a subprocess to prove the core bootstraps with
  PySide6 absent from `sys.modules`.
- Tests assert that no credential-shaped column exists in the schema and that
  no sensitive value reaches logs or status output.
- No test requires a real NG-MB1.

#### Known limitations recorded
- No schema migration mechanism exists yet.
- SQLite returns naive datetimes on read; a regression test documents this so
  PHASE 04 normalises it deliberately.

### Project preparation
- Established phase-based build strategy.
- Established AGENTS.md as universal coding-agent rules.
- Documented verified NG-MB1 protocol behavior.
- Documented 120-byte MB1 user record.
- Documented live attendance capability.
