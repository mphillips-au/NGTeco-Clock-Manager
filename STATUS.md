# STATUS.md

## Current phase

PHASE 14 — Windows packaging: **complete**. PyInstaller `onedir` builds
(development console build and release windowed build), an Inno Setup 6
installer, HKCU startup registration, firewall/network guidance and the
release procedure are documented in `PACKAGING.md`. Verified end-to-end in
this session on this machine: release build compiled, installer compiled,
silent install (`/VERYSILENT`), launch (`--version`, `--headless`
bootstrap against the installed `%LOCALAPPDATA%\Programs\NGTeco Clock
Manager\clockmanager.exe`), Start Menu shortcut creation, and silent
uninstall — confirmed the uninstaller removes the program directory and
Start Menu shortcuts while leaving
`%LOCALAPPDATA%\NGTecoClockManager` (database, logs, backups) completely
untouched.

PHASE 13 — Settings hub: **complete**. Device settings, diagnostics and
account administration are now sections of one role-filtered Settings screen,
alongside General, Payroll and Security. One new permission
(`MANAGE_PAYROLL`, administrator-only) gates pay-schedule changes.

## Next phase

Not yet chosen. Candidates: multi-device support, or the headless Synology
service.

## What exists now

Python 3.12 package `clockmanager` under `src/`, installed editable
(`pip install -e .[gui,dev]`).

Layer separation is in place and enforced by tests:

- `clockmanager.domain` — `Privilege` (0 Employee / 14 Admin), `PunchDirection`
  (0 IN / 1 OUT), `DeviceIdentity`, `DeviceInfo`, `DeviceUser`,
  `AttendanceEvent`, plus user-management rules: `UserDraft`,
  `CredentialAction`, `UserChange`, `UserWriteOutcome`, `describe_changes`,
  plus PHASE 05 business models: `Employee`, `PaySchedule` /
  `PayScheduleType` (weekly/biweekly/semimonthly/monthly), `TimesheetRules`,
  `PayPeriod`, `PunchInput`, `DailySummary` / `PeriodSummary` / `Timesheet`,
  `build_timesheet`, `interpret_naive`, `format_hours`
- `clockmanager.persistence` — SQLAlchemy 2.0 ORM, SQLite engine, forward-only
  migrations, `DeviceRepository`, append-only `AuditRepository`
- `clockmanager.protocol` — NG-MB1 core: `AttendanceDevice` and
  `WritableUserDevice` interfaces, `NGTecoMB1Device`,
  `DeviceConnectionSettings`, the 120-byte user parser **and builder**,
  attendance and live-event parsers, capability model, retry/reconnect,
  structured exceptions, `MockAttendanceDevice`, plus read-only LAN
  discovery (`protocol/discovery.py`: TCP probe, subnet scan, safe
  connect-read-disconnect identification — no write operation exists there)
- `clockmanager.sync` — deterministic event keys (SHA-256 over the natural
  key), pure reconciliation planning and the `SyncSource`
  (`historical`/`manual`/`live`/`background`/`recovery`) vocabulary
- `clockmanager.services` — `bootstrap()`, `ApplicationContext`,
  `ApplicationStatus`, `DeviceService` (profiles, last-seen stamps,
  per-device status, discovery wrappers, explicit `register_discovered`,
  traced builds for diagnostics), `UserService`, `AuditService`,
  `AuthService`, `SyncService`,
  `EmployeeService`, `TimesheetService`, `ReportService`, `BackupService`
  (zip backup, preview/validation, confirmed restore, offline report),
  `DiagnosticsService` (timed connection reports, protocol traces with
  TX/RX + raw/parsed records + live window, sanitized JSON export),
  `MockDeviceFactory`
- `clockmanager.domain.reports` — `ReportType` (8 kinds), `ReportFilter`,
  `Report`, `ExportFormat` (CSV/XLSX/PDF/JSON) plus dependency-free
  XLSX/PDF renderers; every export audited as `report.export`
- `clockmanager.security` — redaction helpers
- `clockmanager.diagnostics` — structured JSON logging with a redacting filter
  on every handler
- `clockmanager.gui` — PySide6 application. `theme.py` holds one `Palette`
  per light/dark theme and generates the whole stylesheet from those tokens;
  `icons.py` draws navigation icons and initials avatars at runtime (no image
  assets); `views/common.py` holds the shared page header, table, empty-state,
  confirmation and toast helpers every view uses; `views/charts.py` paints the
  dashboard trend with no charting dependency. Focus rings appear only for
  keyboard navigation (`FocusVisibilityFilter` in `app.py`). The theme choice
  lives in `QSettings`, as does the optional remembered login username — never
  a password. Navigation shell plus Dashboard,
  Users, Attendance, Live events, Employees, Timesheets, Reports, Device settings,
  Audit log, Diagnostics and Backup
  views; the only subpackage allowed to import PySide6. Device settings hosts
  read-only discovery (check one address, scan the LAN, register by name);
  Backup (admin-only) creates/previews/restores backups and shows offline status.
  Diagnostics (admin-only) runs timed connection reports, full protocol traces
  with redacted raw detail, and sanitized JSON exports. Device settings,
  Diagnostics and User accounts are reached through the Settings hub
  (`views/settings.py`), which shows General, Device, Payroll, Security and
  Developer sections filtered by role: office staff and viewers get General
  and a read-only Payroll only. Payroll surfaces the pay-schedule
  administration `TimesheetService` already had, gated by
  `Permission.MANAGE_PAYROLL` (administrators only).

Entry point `clockmanager` starts the GUI; `clockmanager --headless` runs the
same bootstrap without importing PySide6.

Set `CLOCKMANAGER_USE_MOCK_DEVICE=1` to run the whole application against the
built-in mock device with no hardware attached. The mock now keeps its contents
for the life of the application context, so the write path can be exercised end
to end without a clock.

Database schema version 7: `schema_info`, `devices`, `device_users`,
`attendance_events`, `audit_events`, `sync_history`, `employees`,
`employee_device_links`, `pay_schedules`, `app_users`. No user credential, card or biometric
column exists in any of them; `app_users.password_hash` holds a salted
PBKDF2 hash only (second allowed sensitive column alongside the device
communication password — see `SECURITY.md`).

Schema 6 (`app_users`) is PHASE 07. Schema 7 adds `devices.last_seen_at`
(PHASE 08): the last successful contact, stamped by connection tests and
successful syncs, `None` until a device answers.

## User management (PHASE 03)

The Users view lists, searches and filters the users on the device, and can add,
edit and delete them.

**Device writing is off by default.** It is enabled per installation with
`CLOCKMANAGER_ENABLE_DEVICE_WRITES=1`; writing a PIN needs the further
`CLOCKMANAGER_ENABLE_CREDENTIAL_WRITES=1`. With writing off, the buttons are
disabled and the view explains why.

What the write path does:

- builds an exact 120-byte MB1 record. `pyzk.set_user()` builds a 72-byte packet
  and is never called; a test asserts that against the parsed syntax tree.
- performs read → validate → build → send → verify acknowledgement → read back
  → compare, inside the adapter so a caller cannot skip a step
- compares the stored record byte-exact outside the credential region, and on
  presence within it
- never retries a write
- preserves the credential region byte-for-byte on any change that does not
  explicitly target it, so renaming a user cannot destroy their PIN
- refuses a privilege other than 0 or 14, a field that does not fit the device's
  byte budget, a duplicate user ID and a UID that is not on the device
- records an audit entry for every attempt, including refusals and failures

Deleting shows the exact user read live from the device, the number of
attendance records it has on the device, and a warning that those records are
not deleted; it requires an explicit confirmation, verifies the removal by
re-reading, and audits it.

## Attendance synchronisation (PHASE 04)

Attendance is stored locally and reconciled against the device on every sync.
The MB1 exposes no incremental API, so each sync re-reads the whole device
log and inserts only what is not already stored.

What each sync does:

- reads the device's user list (for the employee-name snapshot) and its full
  attendance log, then always disconnects
- plans inserts with deterministic event keys (SHA-256 over the natural key),
  falling back to the natural key for rows stored before keys existed
- stores every punch, including unknown user IDs (employee snapshot `None`,
  resolved in PHASE 05) — nothing is dropped for being unknown
- records a `sync_history` row: mode, source, seen/new/duplicate counts,
  outcome and error

Sources: `historical` (initial full sync and incremental re-reads), `manual`
(operator "Sync now"), `live` (live-capture punches), `background` (periodic
automatic sync when the profile's interval has elapsed), `recovery` (the first
successful run after a failure, which re-reads everything missed offline).

Device failures are returned as failed results and recorded in the history,
never raised past the GUI; the next successful run picks up whatever was
missed, which is the offline recovery and the missed-live-event recovery.
Direction is derived from `punch` on display; `status` is stored verbatim.

## Verified before this repository build-out

The real NGTeco NG-MB1 was experimentally verified to support:
- TCP 4370 connectivity
- device info retrieval
- historical attendance retrieval
- live attendance capture
- custom 120-byte user parsing
- first/last-name retrieval
- Employee/Admin privilege detection
- basic user record structure inspection

Known device:
- Model: NG-MB1
- Platform: ZMM510_TFT
- Firmware: Ver 8.0.4.5-7108-02

## Known protocol limitations

- Generic pyzk user parsing is not correct for MB1.
- Generic pyzk user writing is not approved for MB1 and is never used.
- The credential region's internal layout is unknown.
- Biometric / card work is investigation-gated (PHASE 11): `READ_FINGERPRINT`
  and `READ_FACE` stay UNVERIFIED, `WRITE_USER_CARD` stays UNSUPPORTED and
  cannot be unlocked, and the adapter exposes no template/enrollment/card
  operation. The per-capability evidence map (command, payload, response,
  structure, confidence, reversibility, test status) is in `PROTOCOL.md`.
  Nothing was implemented because nothing is proven on the project MB1:
  no hardware was available in the investigation session, so no packet
  capture was taken and no disposable test user was exercised.

## Known limitations of the current build

- **Nothing has been run against the real NG-MB1.** The adapter is covered by
  fixtures, a fake transport and a mock device only. The opt-in integration
  suites exist but have not been executed. Treat the adapter as unproven on
  hardware until they are.
- **The write path is UNVERIFIED.** It is built on the verified 120-byte record
  and is covered by unit tests, but no MB1 has accepted a record from it.
  `WRITE_USERS` and `DELETE_USERS` are `UNVERIFIED`; unlocking them reports
  `OPERATOR_ENABLED`, never `SUPPORTED`. Running
  `tests/integration/test_real_device_writes.py` with
  `CLOCKMANAGER_TEST_ALLOW_WRITES=1` against a device, with disposable
  `ZZTEST-` accounts, is what would change that.
- **The PIN offset inside the credential region is a guess.** Bytes 3:11 are
  inferred from the generic ZKTeco record layout. Everything outside that field
  is preserved, but a wrong guess could still leave a test user unable to enter
  their PIN. Only ever exercise it on a disposable account.
- The UID assigned to a new user is the lowest free one this application can
  see. Whether the MB1 accepts an application-chosen UID is unverified.
- Attendance record size (8/16/40) on a real MB1 is still unconfirmed; the
  parser resolves it at runtime and refuses ambiguous payloads.
- The live-capture loop depends on pyzk's name-mangled `_ZK__sock` and
  `_ZK__ack_ok`, and the write path on `_ZK__send_command`. pyzk is pinned to
  `==0.9` because of this.
- `has_credential_data` reports only that the credential region holds non-zero
  bytes. That this always means "a PIN is set" is UNVERIFIED.
- `set_time`, `clear_attendance` and both biometric operations remain
  unavailable. `clear_attendance` and factory reset do not exist in the adapter
  at all. "Remove device" in the GUI deletes the local record only.
- **The device communication password is stored unencrypted at rest.** It is
  kept out of logs, `repr` and the GUI, but the data directory's OS permissions
  are the only control on the stored value. See `SECURITY.md`.
- The audit actor is the logged-in username, falling back to the
  operating-system account when nobody is logged in (headless CLI,
  pre-login). Diagnostic detail is gated on the admin role *and*
  `developer_mode`; the Developer menu is admin-only.
- **Local accounts protect the GUI, not the data directory.** Headless mode
  (`--headless`) performs no login, and anyone with the data directory can
  read the SQLite file. OS permissions on the data directory remain the
  boundary, as with the device communication password.
- No login throttling or lockout: failed attempts are audited, but rapid
  guessing is not slowed. PBKDF2 (210k iterations) is the only cost imposed.
- `requester_role=None` keeps the legacy un-enforced path for callers
  without an interactive identity (tests, background sync timer). The GUI
  always passes the logged-in role; mandatory enforcement with no bypass is
  a follow-up once every caller carries identity.
- `clockmanager.sync` now implements keys, reconciliation and sources; the
  headless/Linux service path uses it through `context.sync` like the GUI.
- Attendance is stored locally and reconciled on every sync; the Attendance
  view works offline. Nothing has been run against the real NG-MB1 yet — the
  sync is proven against fixtures, the fake transport and the mock device
  only.
- Reports and exports are derived read-only views (PHASE 06); raw
  attendance, sync history and audit rows are never mutated by building or
  exporting. Windows packaging/installer is complete (PHASE 14); see
  `PACKAGING.md`.
- SQLite returns naive datetimes on read. `received_at` is normalised to
  aware UTC on read (`as_aware_utc`); `occurred_at` stays naive deliberately
  because it is device-local time with no known timezone — do not label it
  UTC. Event keys normalise both forms to the same wall-clock seconds, so
  duplicate detection is unaffected.
- The employee snapshot on stored punches is a display-name string, not a
  link. PHASE 05 resolves the real employee independently: timesheets match
  stored punches to employees through the canonical user ID plus every linked
  device user ID, so the snapshot never affects calculation.
- LAN discovery has not been run against real hardware: probing and safe
  identification are proven against loopback sockets and the mock device
  only. A scan finds candidates; only a connection test or sync proves one.
- Backup restore migrates an older database forward and refuses a newer
  schema outright; cross-version restores beyond that are untested.
  Restoring replaces the live database file contents in place — the safety
  backup is the way back.
- Transport-level TX/RX capture runs only against real hardware and, like
  everything else here, has not touched a real NG-MB1: it is proven against
  a fake transport, and mock traces honestly time device operations
  instead of showing packets.

## Employees / timesheets (PHASE 05)

Employees are business records above device users: internal ID, canonical
user ID, names, active flag, department, position, email, notes, plus
per-device `(device, user ID)` links so one person can map to different IDs
on different clocks. Deactivation is a flag, never a delete; every
create/update/(de)activate/link is audited.

Pay schedules cover weekly, bi-weekly, semi-monthly and monthly cadences in
an explicit IANA timezone, with day-cutoff hour, duplicate interval,
maximum shift length, HH:MM/decimal display and optional daily/weekly
overtime thresholds. Exactly one schedule is active.

Timesheets are derived on demand from immutable stored attendance and are
recalculable: pairing is global and chronological (an overnight IN->OUT pair
stays one shift attributed to the IN day), duplicates within the configured
interval are flagged and excluded, and each day reports first IN, last OUT,
worked time, missing/duplicate/excessive/overnight flags and overtime.
Naive device-local times are interpreted as wall time in the schedule's
timezone; durations are real elapsed time measured in UTC, so DST
transitions total correctly.

## Reports / exports (PHASE 06)

Eight derived reports over immutable data: daily attendance, employee
timesheet, weekly summary, pay-period summary, exceptions
(missing/duplicate/excessive/overnight/unknown punch, using the same
pairing rules as timesheets), device activity, sync history and audit
  trail. Filters: date range, employee, user ID, device, department,
  punch/status, exceptions-only. Exports to CSV, XLSX, PDF and JSON use
  dependency-free renderers and are audited as `report.export`. Schema
  still version 5: reports add no tables. Verified against SQLite and the
  mock context; no real-device run.

## Authentication / roles (PHASE 07)

Local accounts with three roles (`domain/auth.py` is the single matrix both
layers decide from): Admin (everything, incl. device settings, diagnostics
and User accounts), Office staff (employees, timesheets, reports, sync,
live capture, audit log — no device settings/diagnostics/accounts/device
user writes), Viewer (six read-only views; sync and live capture disabled).

Passwords are salted PBKDF2-HMAC-SHA256 hashes (stdlib, 210k iterations,
16-byte salt); plaintext exists only for one hash/verify call and never
reaches logs, `repr` or audit details. First run creates the initial admin;
afterwards the GUI requires login and Logout returns to it (audited as
`auth.login`/`auth.logout`, failures included). The audit actor is the
logged-in username, falling back to the OS account headless/pre-login.
Service mutations take an optional `requester_role` and refuse roles
without the permission; `None` keeps the legacy path for callers without
an interactive identity (tests, background sync).

## Device management / discovery (PHASE 08)

Multi-device records carry connection settings plus locally observed state:
last seen (`devices.last_seen_at`, schema 7), firmware/platform/serial
(stored on every successful contact) and sync state (stored counts plus the
append-only sync history). One device ships first; nothing hardcodes it.

Discovery is read-only: check one address, scan the local subnet for TCP
4370, identify safely (connect, read snapshot, disconnect — no write
operation exists in `protocol/discovery.py`, pinned by a test). Scans refuse
ranges over 1024 addresses. A discovery never becomes a profile on its own:
`register_discovered` needs an operator-supplied name, refuses duplicate
names and already-stored addresses, and writes locally only. The Device
settings view shows last-seen/sync state per profile and hosts the
discovery controls; Backup stays admin-only, so office staff and viewers
never see discovery at all.

## Backup / offline resilience (PHASE 09)

One zip per backup: full database copy (via the SQLite online-backup API),
`config.json`, portable exports (employees CSV/JSON, device-users JSON,
attendance CSV, audit CSV, sync history JSON) and a manifest with counts
and a sensitive-content note. Portable exports carry no communication
password, PIN, card or biometric data (exact keys pinned by tests); the zip
itself is administrator-only because the database copy holds the stored
device connection secrets.

Restore is preview → validate → confirm: `preview_backup` reads only and
refuses non-zips, missing files, bad manifests and newer schemas;
`restore_backup` additionally requires `confirmed=True`, takes an automatic
`pre-restore` safety backup, migrates older databases forward, and audits
`backup.create` / `backup.restore`. Without confirmation nothing changes.

Offline: every view reads local storage, so history, employees, timesheets,
reports and audit keep working with the clock down; `offline_report` (shown
in the Backup view) lists per-device stored counts, last-seen and last sync,
and the next successful sync re-reads the whole device log — the PHASE 04
recovery path — picking up whatever was missed.

## Developer diagnostics (PHASE 10)

Admin-only protocol diagnostics, read-only by construction. A connection
report times connect, clock read and a disconnect/reconnect cycle per step.
A protocol trace captures one connected session: transport TX/RX on real
hardware (a recording wrapper around the pyzk transport; the mock has no
socket, so its trace times device operations instead), raw + parsed user
records with the credential region zeroed inside the protocol layer, raw +
parsed attendance, a short live-capture window (0-30 s, capped at 50
events) and the capability report. Device failures arrive as failed
results; only role refusal raises. Traces export as sanitized JSON,
audited as `diagnostics.export` with counts only. The mock carries raw
user records like the adapter and a raw attendance payload when its script
is given a fixture one; diagnostics builds devices without write unlocks
and offers no write/delete/clear/set-time operation anywhere (pinned by
tests, including a GUI check that no such button exists).

## Windows packaging (PHASE 14)

Full detail lives in `PACKAGING.md`; summary here for cross-reference.

- **Build system**: PyInstaller `onedir` builds driven by
  `packaging/clockmanager.spec` and orchestrated by `packaging/build.py`
  (`--dev`, `--release`, `--installer`, `--verify`, `--all`, `--clean`).
  The dev build keeps a console window (`clockmanager-dev.exe`); the
  release build is windowed with an embedded PE version resource and icon
  (`clockmanager.exe`).
- **Installer**: `packaging/installer.iss` (Inno Setup 6), per-user
  install under `%LOCALAPPDATA%\Programs\NGTeco Clock Manager`
  (`PrivilegesRequired=lowest`, no UAC needed), optional desktop/Start Menu
  shortcuts and an optional "launch at Windows startup" task. Uninstall
  only ever removes `{app}` — the user data directory is never touched by
  install, upgrade or uninstall.
- **Data isolation**: application binaries live under
  `%LOCALAPPDATA%\Programs\NGTeco Clock Manager`; database, config, logs
  and backups live under the existing `%LOCALAPPDATA%\NGTecoClockManager`
  `AppPaths` directory (unchanged from earlier phases), so upgrades never
  risk the database — the existing forward-only migration path
  (`initialise_database`) handles schema upgrades on next launch.
- **Windows platform integration**: new `clockmanager.windows` module
  (stdlib-only, no PySide6 import, keeps GUI-import layering) providing
  HKCU `Run` key startup registration (`is_startup_enabled`,
  `set_startup_enabled`, `get_startup_command`) and firewall/network
  guidance (`get_firewall_guidance`: TCP/UDP 4370, PowerShell
  `New-NetFirewallRule` snippets). Exposed on the CLI as
  `--enable-startup` / `--disable-startup` / `--status-startup` /
  `--firewall-info` in `clockmanager.__main__`.
- **Versioning**: semantic version kept in sync across `pyproject.toml`,
  `src/clockmanager/__init__.py`, `packaging/installer.iss` and
  `packaging/version_info.txt`; consistency is enforced by
  `tests/unit/test_packaging.py`.
- **Verified this session**: `python packaging/build.py --release` builds
  successfully (PyInstaller 6.22.2, Python 3.12 venv), and
  `python packaging/build.py --verify` passes — `--version`,
  `--firewall-info` and `--headless` (real bootstrap: config/DB/log paths
  under `%LOCALAPPDATA%\NGTecoClockManager`, schema 7, audit log active)
  all succeed against the compiled `dist\clockmanager\clockmanager.exe`.
  Full test suite (804 tests), ruff and mypy all pass. Inno Setup 6 was
  located (`%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`) and
  `python packaging/build.py --installer` compiled
  `NGTecoClockManager-Setup-0.13.0.exe` (~38 MB) successfully. The full
  install lifecycle was exercised end-to-end on this machine: silent
  install (`/VERYSILENT /SUPPRESSMSGBOXES`) placed the exe under
  `%LOCALAPPDATA%\Programs\NGTeco Clock Manager` and created the Start
  Menu shortcuts; the installed `clockmanager.exe --version` and
  `--headless` both ran correctly and created the data directory as
  expected; silent uninstall then removed the program directory and Start
  Menu shortcuts while leaving `%LOCALAPPDATA%\NGTecoClockManager`
  (database, logs, backups) completely untouched, confirming the
  data-preservation guarantee. No real NG-MB1 hardware was available in
  this session, so the device-connection step of the release checklist
  remains unverified against real hardware (consistent with every prior
  phase — see "Verified before this repository build-out").

## Protocol discoveries

### PHASE 01

Recorded in `PROTOCOL.md` under "pyzk integration boundary". Established by
reading pyzk 0.9 source against the verified MB1 layout, not by device testing:

- `ZK.get_users()` silently parses 120-byte MB1 records in 72-byte strides
  instead of failing.
- `ZK.get_attendance()` and `ZK.live_capture()` both call that broken
  `get_users()` internally, so neither can be used as-is on an MB1.
- `ZK.live_capture()` additionally evaluates `int(user_id)` for any unknown
  user, which raises on a non-numeric MB1 user ID such as `EMP-003`.
- Attendance payload length alone cannot identify the record size, because
  every multiple of 40 is also a multiple of 8 and 16.

### PHASE 03

Also established from the pyzk 0.9 source, not from device testing:

- `ZK.set_user()` builds `pack("HB8s24s4sx7sx24s", ...)` for any device whose
  `user_packet_size` is not 28 — a **72-byte** packet. The MB1 record is 120
  bytes with a different layout, which is the concrete proof that the generic
  writer cannot be used.
- `ZK.set_user()` and `ZK.delete_user()` both call `refresh_data()`
  (`CMD_REFRESHDATA`, 1013) after the write. The MB1 adapter does the same, and
  treats a failed refresh as leaving the device in an unknown state rather than
  as a successful write.
- `ZK.delete_user()` falls back to `get_users()` when given a user ID rather
  than a UID, inheriting the broken parse, so the adapter always resolves the
  UID itself from the 120-byte parser first.

## Rules for updates

Keep this file concise.
Update it at the end of every coding session.
