# STATUS.md

## Current phase

PHASE 16 — Synology / Linux headless service: **complete** (see "Headless
service (PHASE 16)" below). No real-device run and no Synology hardware was
available; the Docker image is unrun on a NAS.

PHASE 15 (wiring) — the proven capabilities are now **in the application**.
The four reads PHASE 15 proved and left unused are wired through every layer:
device settings (`CMD_OPTIONS_RRQ`, allow-listed and read-only), capacity and
usage (so every snapshot says "6 of 30,000 used" rather than "6"), per-user
fingerprint enrolment on the Users screen, and the device's own keypad
operation log. Settings ▸ Device settings gained a read-only "Device
information" tab, and the diagnostics trace and export gained the same three
sections. Nothing on the PHASE 15 trap list was implemented, and no new write
of any kind was added — `WRITE_DEVICE_OPTIONS` is UNSUPPORTED and cannot be
operator-unlocked. Full entry in `CHANGELOG.md`.

PHASE 15 — Capability investigation: **complete**. The first session to write
to the real NG-MB1. The write path, the PIN offset and fingerprint enumeration
all graduated from UNVERIFIED to proven; the read-back comparison that had been
hiding every successful write was fixed; and one write cost the device both of
its enrolled fingerprints and forty minutes of protocol downtime. The full
report, evidence map, prioritised roadmap and incident write-up are in
`phases/PHASE-15.md`.

> **Outstanding physical actions for the operator.** Both fingerprints need
> re-enrolling at the device, and a leftover `ZZTEST-LONGID` user at UID 901
> must be deleted from the keypad — it cannot be deleted over the protocol.
> Two further questions answer themselves the next time somebody is at the
> clock: one deliberate punch by finger and one by face would settle what
> `status` means, and any punch at all settles which live-event layout this
> firmware sends (the body size is already logged when one arrives).

> **Merged.** The PHASE 14 QA work reached `main` as PR #11; this phase was
> rebased onto it, so `main` is linear through PHASE 15.

PHASE 14 (QA) — Production QA: **complete**. The application was exercised
end to end against the real NG-MB1 for the first time; four defects were found
and fixed (see the CHANGELOG). Hardening only, no feature added.

> **Numbering note.** Three sessions ran in parallel and two independently
> took the number 14. "PHASE 14 — Windows packaging" below is the
> `phases/PHASE-13.md` brief; "PHASE 14 (QA)" is the `phases/PHASE-14.md`
> brief (now `phases/complete/PHASE-14.md`). Both are complete.
>
> PHASE 15 straightened the rest: the capability investigation is
> `phases/PHASE-15.md`, and the three planned briefs moved up one —
> Synology/headless is `PHASE-16.md`, Web/API `PHASE-17.md`, web frontend
> `PHASE-18.md`. Their content is unchanged.

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

PHASE 17 — Web/API boundary (in progress in a parallel session; see the
`--serve` / `--api-serve` note under "Headless service (PHASE 16)" below).
The prioritised roadmap with evidence and effort estimates is in
`phases/PHASE-15.md`; its top items are the read-only device-settings panel
built on `CMD_OPTIONS_RRQ` and an Australian payroll export.

## Headless service (PHASE 16)

Complete. The proven device/sync core runs without the Windows GUI as a
Linux/Synology headless service. Verified against SQLite and the mock
context; no real-device run.

- **Service loop** (`clockmanager.headless.runner.HeadlessService`): each
  pass runs `SyncService.background_sync_if_due` for every enabled,
  configured device, so reconciliation, duplicate-safe inserts and offline
  recovery are the exact code the GUI uses. No protocol code is duplicated
  (pinned by a test: the package never imports the transport or parsers).
- **Reconnect**: a failed sync is a result, never an exception. Consecutive
  failures hold the device out of the loop on a 30 s doubling backoff
  capped at 10 minutes; the service never gives up, and an unexpected
  exception is logged without stopping the pass.
- **Live capture** (opt-in, off by default): one worker thread per device
  stores live punches with a one-time name snapshot; anything missed is
  recovered by the next periodic pass. Stops cooperatively on shutdown.
- **Health endpoint** (stdlib only, no new dependency): `GET /health`
  (liveness, always 200 while serving) and `GET /ready` (200 after the
  first pass completes, 503 until then). The payload is device names,
  counts and timestamps only — no communication password, PIN, card or
  biometric value.
- **Lifecycle**: `clockmanager --serve` runs until SIGTERM/SIGINT (Docker
  `ENTRYPOINT`), `--serve-once` runs one pass for cron/systemd timers.
  Start/stop are audited as `service.start` / `service.stop`. New
  configuration keys `service_poll_seconds` (default 60),
  `service_health_bind` (default `127.0.0.1:8080`, empty disables) and
  `service_live_capture`, each overridable by `CLOCKMANAGER_SERVICE_*`
  environment variables and by `--interval` / `--health-bind` /
  `--live` / `--no-live` flags.
- **Docker**: `Dockerfile` (python:3.12-slim, GUI-free install, non-root
  user, `/data` volume, `HEALTHCHECK` against `/health`) plus a
  Synology-Container-Manager-compatible `docker-compose.yml`.

> **CLI flag note for the PHASE-17 session.** This phase defined
> `--serve` as the headless sync loop (Docker contract, tests, docs).
> A parallel session added a second `--serve` for the web/API boundary,
> which broke the argument parser for every CLI test. It is now
> `--api-serve` (same behaviour, `--api-host` / `--api-port`
> unchanged). If the API is meant to start the sync loop itself rather
> than sit beside it, say so and the two flags can be reunified.

Two of them are physical and only the operator can do them: re-enrol both
fingerprints, and delete `ZZTEST-LONGID` at UID 901 from the device keypad.

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

Database schema version 8: `schema_info`, `devices`, `device_users`,
`attendance_events`, `audit_events`, `sync_history`, `employees`,
`employee_device_links`, `pay_schedules`, `app_users`. No user credential, card or biometric
column exists in any of them; `app_users.password_hash` holds a salted
PBKDF2 hash only (second allowed sensitive column alongside the device
communication password — see `SECURITY.md`).

Schema 6 (`app_users`) is PHASE 07. Schema 7 adds `devices.last_seen_at`
(PHASE 08): the last successful contact, stamped by connection tests and
successful syncs, `None` until a device answers. Schema 8 (PHASE 14) adds no
column: it clears `attendance_events.device_uid`, which held an attendance
record index rather than a user UID on every MB1 read. Punches, identities
and event keys are untouched.

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
- The credential region is partly understood (PHASE 15): bytes 3:11 are the
  PIN as NUL-padded ASCII, proven on hardware. Bytes 11:35 have no known
  meaning and are always preserved.
- **The device does not store the bytes it is given.** It sets byte 87 itself
  and does not zero-fill field tails, so read-back verification compares
  decoded fields rather than bytes. Byte-exact comparison fails on every write
  to this device.
- **Writable field budgets are smaller than the record regions.** User ID 9
  bytes (`~PIN2Width`), last name 23 bytes. Exceeding the first is what caused
  the PHASE 15 incident.
- Fingerprints can be **enumerated** but not read, written or enrolled
  (PHASE 15). `READ_FINGERPRINT` is SUPPORTED for enumeration only;
  `READ_FACE` stays UNVERIFIED because no command is known; `WRITE_USER_CARD`
  stays UNSUPPORTED and cannot be unlocked. The per-capability evidence map
  (command, payload, response, structure, confidence, reversibility, test
  status) is in `PROTOCOL.md`.
- The device exposes a large read-only option surface (`CMD_OPTIONS_RRQ`) and
  its own operation log (`FCT_OPLOG`, 33 records) that the application does not
  touch at all. Both are safe reads; see the PHASE 15 roadmap.
- **The device offers no incremental-sync handle**: every `*Stamp` change
  counter is refused. Full re-reads remain the only way to reconcile.
- `IPAddress` read from the device is **stale** (reports 192.168.1.201 while
  the device answers at 192.168.0.16). Never reconnect or scan from it.

## Verified on hardware (PHASE 14)

The application was exercised end to end against the real NG-MB1
(serial NBF6260700048, ZMM510_TFT, Ver 8.0.4.5-7108-02, 192.168.0.x).
Everything below actually ran against the device, not a fixture:

- **Discovery** — a /24 scan found the clock on TCP 4370 and identified it
  safely (connect, read, disconnect).
- **Connect / reconnect** — connection report: connect 155 ms, clock read
  5 ms, drop-and-reconnect 156 ms.
- **120-byte user parse** — the enrolled user read back with the correct UID,
  user ID, first/last name, privilege 14 (Admin) and `has_credential_data`.
- **Attendance** — 40-byte records confirmed; punch 0 = IN, 1 = OUT; `status`
  values 1 and 15 both preserved verbatim.
- **Idempotence** — three consecutive syncs: 3 new, then 0 new, then 0 new.
- **Live capture** — a real badge produced a live event (user `1`, OUT,
  20:27:30) which stored with source `live`.
- **Live recovery** — the following full sync read 4 records and inserted 0:
  the live punch matched the device's own historical row by event key, so a
  punch captured live is not duplicated when the log is re-read.
- **Diagnostics / TX-RX** — a full protocol trace recorded genuine CMD 9 and
  CMD 13 payloads with the credential region zeroed. No credential byte from
  the device's non-empty credential region appeared in the trace, the
  sanitized export or the log file.
- **Offline** — with an unreachable address the sync failed as a result (not
  an exception), was recorded in the history, and every local view kept
  working; the next successful run re-read the whole log.
- **Roles, backup/restore, migrations, reports/exports, timesheets/DST** were
  verified alongside, against local storage (see the PHASE 14 CHANGELOG entry).

- **Packaging** — release build and Inno Setup installer compile against these
  changes; the frozen exe is code-page safe; a schema-7 database migrated to 8
  in place under the packaged executable with every device, employee, punch
  and event key intact; silent install/uninstall leaves
  `%LOCALAPPDATA%\NGTecoClockManager` untouched; `pyzk` and
  `clockmanager.protocol` ship inside the installed binary.

Still unproven on hardware: the **write path** (create/update/delete/PIN) and
everything biometric or card related. Those stay locked and UNVERIFIED.

## Known limitations of the current build

- The **read path is now proven on the real NG-MB1** (PHASE 14, serial
  NBF6260700048): discovery, connect, device info, clock read, the 120-byte
  user parser, attendance retrieval, sync idempotence, diagnostics and TX/RX
  capture all ran against the hardware. The **write path is still unproven** —
  see the next entry. Anything not listed under "Verified on hardware (PHASE
  14)" below remains fixture-proven only.
- **The write path is PROVEN (PHASE 15).** The real NG-MB1 accepted
  application-built 120-byte records: create, rename, privilege 0 -> 14 -> 0,
  PIN set, rename with the PIN preserved, PIN clear and delete were all
  verified by read-back on disposable `ZZTEST-` accounts. `WRITE_USERS`,
  `DELETE_USERS` and `WRITE_USER_PASSWORD` are `SUPPORTED`.
- **Support is not permission.** Writing is still off by default. A device
  built without `CLOCKMANAGER_ENABLE_DEVICE_WRITES` reports those capabilities
  as `OPERATOR_LOCKED` — proven, not usable here — and the adapter refuses
  them. The new `Support.OPERATOR_LOCKED` state exists precisely so that
  graduating a capability cannot silently switch writing on everywhere.
- **The PIN offset is proven, not guessed** (PHASE 15): bytes 3:11, NUL-padded
  ASCII. Setting, preserving and clearing were each verified on hardware.
- **The MB1 accepts an application-chosen UID** (PHASE 15): a record built with
  UID 900 was stored, read back and deleted cleanly. The application still uses
  the lowest free UID by default.
- **One record on the device cannot be deleted.** `ZZTEST-LONGID` at UID 901,
  written with an over-long user ID during PHASE 15, survives
  `CMD_DELETE_USER` (acknowledged, ineffective) across reconnects and a reboot.
  It has no PIN, fingerprint or face, so it cannot clock in. Removal needs the
  device keypad.
- Attendance record size on the project MB1 is **40 bytes**, confirmed on
  hardware (PHASE 14). The parser still resolves the size at runtime from the
  device's record count and still refuses ambiguous payloads, because other
  firmware in the family may differ. The count is load-bearing, not
  belt-and-braces: a 3-record 40-byte body is 120 bytes, which also divides by
  8, so length alone is ambiguous in the ordinary case.
- The live-capture loop depends on pyzk's name-mangled `_ZK__sock` and
  `_ZK__ack_ok`, and the write path on `_ZK__send_command`. pyzk is pinned to
  `==0.9` because of this.
- `has_credential_data` means "this user has a PIN", proven in both directions
  on hardware (PHASE 15): all-zero when created without one, non-zero after
  setting, zero again after clearing.
- Attendance `status` is still opaque. Values 1 and 15 were observed and match
  the standard ZKTeco fingerprint/face verification codes on a device with
  exactly one of each per user, but that is inference: it needs one deliberate
  punch by each modality to prove. Direction is never inferred from it.
- Which live-event body size this firmware sends is still undetermined; it
  needs a real badge. The adapter now logs the observed size so the next punch
  settles it.
- `set_time`, `clear_attendance`, face reads and every fingerprint template
  operation remain unavailable. `clear_attendance` and factory reset do not exist in the adapter
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
  view works offline. Sync is now proven on the real NG-MB1 (PHASE 14):
  three consecutive syncs inserted 3, then 0, then 0.
- Reports and exports are derived read-only views (PHASE 06); raw
  attendance, sync history and audit rows are never mutated by building or
  exporting. Windows packaging/installer is complete (PHASE 14); see
  `PACKAGING.md`.
- The headless service (PHASE 16) only reconciles stored profiles: it never
  creates, discovers or registers a device — that stays an operator action
  in the GUI. The Docker image builds from the documented files but has not
  been run on Synology hardware in this session (no NAS available); the
  `HEALTHCHECK` assumes the default health bind, and a first run with an
  empty data directory serves `/ready` as 503 until a profile exists and a
  pass completes.
- SQLite returns naive datetimes on read. `received_at` is normalised to
  aware UTC on read (`as_aware_utc`); `occurred_at` stays naive deliberately
  because it is device-local time with no known timezone — do not label it
  UTC. Event keys normalise both forms to the same wall-clock seconds, so
  duplicate detection is unaffected.
- The employee snapshot on stored punches is a display-name string, not a
  link. PHASE 05 resolves the real employee independently: timesheets match
  stored punches to employees through the canonical user ID plus every linked
  device user ID, so the snapshot never affects calculation.
- LAN discovery is proven on real hardware (PHASE 14): a /24 scan found the
  project clock on TCP 4370 and identified it safely. A scan still only finds
  candidates; only a connection test or sync proves one.
- Backup restore migrates an older database forward and refuses a newer
  schema outright; cross-version restores beyond that are untested.
  Restoring replaces the live database file contents in place — the safety
  backup is the way back.
- Transport-level TX/RX capture is proven on the real NG-MB1 (PHASE 14): a
  full trace recorded genuine CMD 9 and CMD 13 payloads with the credential
  region zeroed. Mock traces still have no socket and honestly time device
  operations instead of showing packets.

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
