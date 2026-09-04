# CHANGELOG.md

## Unreleased

### PHASE 12 — UI polish (2026-09-04)

Presentation only. No service, protocol, persistence or permission behaviour
changed: every screen calls the same services, off the UI thread, under the
same role gates.

#### Theme

- `gui/theme.py` rewritten around a `Palette` dataclass per theme, with the
  stylesheet generated from those tokens. Previously the dark sheet styled
  only a handful of widgets, so the page background stayed light, card values
  and group-box titles rendered dark-on-dark, and most of the dark theme was
  unreadable. Every rule that paints a background now also sets a foreground.
- Coverage added for menus, tooltips, scrollbars, tabs, date fields, progress
  bars, item-view outlines and dialogs.
- `current_palette()` lets a view colour a single table cell (items carry no
  stylesheet) from the active theme instead of a hardcoded hex value.
- Focus rings are keyboard-only: `FocusVisibilityFilter` (in `gui/app.py`)
  records the Qt focus reason on the widget as `focusVisible`, and the
  stylesheet keys the ring off that. Clicking a button no longer boxes it;
  tabbing to it still does.

#### Shared view vocabulary (`gui/views/common.py`)

- `page_header()` — title, one line of purpose, hairline; used by every view.
- `fill_table(..., empty_message=...)` — a centred, dimmed, unselectable line
  spanning the table instead of a row of em-dashes or an empty white slab.
- `build_table(..., stretch_columns=...)` — named columns absorb spare width,
  the rest size to content, with `setResizeContentsPrecision(20)` so sizing
  does not measure every row of a large table.
- `confirm()` — action-named confirm button ("Delete user", not "Yes"), Cancel
  as the default and escape button.
- `notify()` — brief non-blocking corner toast for completed work; failures
  also stay in the view's status line.
- `primary_button()`, `muted_label()`, `tint_cell()`.

#### Screens

- Icons and avatars are drawn at runtime (`gui/icons.py`): no image assets,
  correct at any DPI, recoloured with the theme. Sidebar navigation icons,
  initials badges for people.
- Main window: fixed navigation row height, `Ctrl+1`…`Ctrl+9` section
  shortcuts, and a signed-in identity block naming the operator and role.
- Dashboard: aligned stat cards with human timestamps ("Last seen today
  09:25", full detail on hover), a painted seven-day IN/OUT trend
  (`gui/views/charts.py`, no charting dependency), recent punches with
  initials badges and IN/OUT colouring, and reference tables that no longer
  show a phantom selected row.
- Reports: free-text `YYYY-MM-DD` fields replaced with calendar pickers and
  named ranges (Today, Yesterday, This week, Last week, This month, Last 30
  days, Custom); filters laid out as a two-column form; export reports the
  saved size and file name.
- Users: search, privilege and "with PIN only" filters (the old privilege
  picker and Admins-only checkbox were the same filter twice); enrolment and
  privilege tinting now theme-aware.
- Attendance: IN/OUT colouring, content-sized columns, filter empty states.
- Device settings: form width capped, `Save` promoted, `Remove device`
  marked as destructive.
- Login: branded header, Enter submits, and a **Remember my username**
  option. Only the username is stored (`QSettings`), never a password.

#### Corrections

- Device settings claimed "this build never writes to a device" even when
  device writing was enabled. It now states only what that screen does.
- Mock attendance is anchored to today rather than a fixed date in March
  2026, so running against the built-in mock device shows a populated
  dashboard and trend. Callers that need stable timestamps still pass
  `reference`.

#### Tests

- Reports date-range tests rewritten for the pickers; `resolve_range()` is
  tested directly as calendar arithmetic.
- New tests for remembering and forgetting the login username.

### PHASE 11 — Biometric / Card Investigation (2026-09-04)

Investigation only: no card, fingerprint or face operation was implemented,
and no capability changed state. Verified against the unit suite; no
real-device run (no hardware was available in the session).

#### Findings (`PROTOCOL.md`, `RESEARCH.md`)

- Per-capability evidence map in `PROTOCOL.md` under "Biometric / card
  investigation (PHASE 11)": command, payload, response, structure,
  confidence, reversibility and test status for presence counters,
  fingerprint bulk/single read, enrollment, upload/delete, face templates
  and the card field.
- `fananimi/pyzk` issue #240 (NG-MB2, same firmware/platform family)
  reports bulk fingerprint read (`CMD_DB_RRQ`/`FCT_FINGERTMP`) compatible
  but single-template read (command 88) incompatible and `enroll_user`
  (`CMD_STARTENROLL`) freezing the device — sibling-model supporting
  evidence only, not production truth.
- The single public card-offset claim (bytes 83:87, 4-byte LE) collides
  with the verified last-name region (59:96) and mis-slices neighbouring
  fields: very-low-confidence hypothesis. `WRITE_USER_CARD` stays
  UNSUPPORTED and cannot be unlocked.
- pyzk 0.9 has no face-template API (presence flags only) and its
  fingerprint upload embeds the generic 72-byte user packet already proven
  wrong for the 120-byte MB1 record: nothing to build on.
- Proving tests are specified per capability (keypad-enrolled disposable
  user + before/after 120-byte record diff for the card; captured bulk
  read against a known enrolled finger for templates) for a future
  hardware session.

#### Tests

- 9 new tests in `tests/unit/test_biometric_investigation.py`: biometric
  reads stay UNVERIFIED and locked under every write unlock, card writing
  stays UNSUPPORTED on both devices, neither the adapter nor the mock
  exposes or calls any template/enrollment/card operation (AST guards),
  discovery defines none, and `DeviceUser` carries no biometric/card field.
- Fixed two misplaced `# noqa: DTZ001` comments in the PHASE 10 suites
  (`test_trace.py`, `test_diagnostics_service.py`): the marker belonged on
  the deliberate naive-`datetime` fixture line, not the following line.
  Behaviour unchanged; ruff had flagged them as unused directives.
- ruff (lint + format) and mypy strict pass clean.

### PHASE 10 — Developer Diagnostics (2026-09-04)

Admin-only protocol diagnostics, read-only by construction. Verified
against SQLite, loopback-free fake transports, fixture payloads and the
mock context; no real-device run.

#### Protocol (`clockmanager.protocol.trace`, PySide6-free)

- Credential-region redaction inside the protocol layer (wrong-sized
  buffers refused, so unknown shapes can never leak), bounded hex previews
  (128 bytes max — one redacted record always shows whole), per-record
  redacted user snapshots, raw + parsed attendance snapshots with a
  parsed-only fallback, and a `TraceRecorder` with per-step timings.
- `RecordingTransport`: wraps the pyzk transport on real hardware and logs
  genuine TX (command + payload size) / RX (byte count + redacted preview)
  traffic; unknown attributes delegate so the adapter works unchanged.
- New `read_raw_attendance_payload()` on the MB1 adapter (read-only;
  attendance bytes hold no credentials) and on the mock, which carries a
  raw payload only when its script is given a fixture one — otherwise
  diagnostics shows parsed attendance with an explanatory note instead of
  inventing bytes. `default_transport` is now public so traced builds can
  wrap it.

#### Services (`DiagnosticsService`, via `context.diagnostics`, PySide6-free)

- `connection_report`: timed connect, device-clock read with drift note,
  and a disconnect/reconnect cycle; stamps identity and last-seen on
  success like a connection test. Device failures are failed results.
- `protocol_trace`: one connected session — transport note, timed steps,
  redacted user records, raw + parsed attendance, an optional 0-30 s live
  listen capped at 50 events, capabilities, closing reconnect — always
  disconnecting, even on failure. New `AuditAction` `diagnostics.export`.
- `export_trace`: the trace as sanitized JSON (redacted hex only; no
  communication password, PIN, card or biometric value by construction),
  audited with counts only.
- Every method requires `VIEW_DIAGNOSTICS` when a role is passed; only
  refusal raises. `DeviceService.build_traced` builds without write
  unlocks and says honestly when there is no packet traffic (mock).

#### GUI

- Diagnostics view (already admin-only via navigation) gains a connection
  report button, a protocol trace button with a 0-30 s live-listen option,
  a monospace redacted-detail pane, and a sanitized-JSON export via save
  dialog. All work off the UI thread; existing checks are untouched. No
  write/delete/clear/set-time control exists anywhere on the view.

#### Tests

- 20 trace tests: redaction (region zeroed, wrong sizes refused), bounded
  previews, user-shaped payload masking, recorder sequencing/timing,
  recording transport against a fake (TX + ACK, redacted RX, delegation),
  user snapshots (parsed fields, truncation caps detail not counts),
  attendance snapshots (raw + parsed, parsed-only fallback, corrupt
  payload error), plus a no-write AST guard.
- 17 service tests: passing/failing connection reports, full mock trace,
  disconnect-always, live-window collection, fixture-payload raw
  attendance, failure events-so-far, argument validation, role refusals,
  capability report, sanitized audited export, failed-trace export,
  traced-build notes and write-lock absence, plus the service no-write
  guard; 7 GUI tests (report steps, redacted trace, live listen,
  JSON export round-trip, export-without-trace, no destructive controls,
  non-admin refusal).
- ruff (lint + format) and mypy strict pass clean.

### PHASE 09 — Backup / Offline Resilience (2026-09-04)

Recoverable, still useful with the clock down. Verified against SQLite and
the mock context; no real-device run.

#### Services (`BackupService`, via `context.backups`, PySide6-free)

- `create_backup(label)`: one zip with a full database copy (SQLite
  online-backup API), `config.json`, portable exports (employees CSV/JSON,
  device-users JSON, attendance CSV, audit CSV, sync history JSON) and a
  manifest with counts plus a sensitive-content note. Audited as
  `backup.create` (new `AuditAction`s `backup.create` / `backup.restore`).
- `preview_backup(path)`: read-only validation — refuses non-zips, missing
  files, bad manifests and newer schemas — and describes what a restore
  would do without changing anything.
- `restore_backup(path, confirmed=True)`: requires an explicit confirmation
  (refused otherwise), takes an automatic `pre-restore` safety backup,
  copies the backup database into the live store, migrates older schemas
  forward, restores configuration, and audits the restore in the restored
  database. A restored database is proven fully usable (reads, syncs, new
  writes).
- `offline_report()`: per-device stored counts, last-seen and last sync
  from local reads only — the Offline status in the GUI.
- Administrator-only when a role is passed (`MANAGE_DEVICE_SETTINGS`):
  the zip holds the full database copy including stored device connection
  secrets. Portable exports never contain a communication password, PIN,
  card or biometric value (exact exported keys pinned by tests).

#### GUI

- New admin-only Backup view (hidden from office staff and viewers):
  offline status, labelled "Create backup now", backup table, select to
  preview, and Restore behind a confirmation dialog that shows the preview
  and requires an explicit "I understand" check. Unconfirmed restores
  change nothing. All work off the UI thread; navigation grows to eleven
  views (twelve for admins with User accounts).

#### Tests

- 20 new service tests: complete bundle contents, manifest counts, audit
  entries, no-secret exports, newest-first listing, disabled-device skip,
  preview validity/invalidity/newer-schema refusal/preview-changes-nothing,
  confirmation gate, full restore round-trip, safety backup, invalid-restore
  refusal, restored-database usability, offline reporting; plus 6 GUI tests
  (offline load, create/list, preview select, cancelled restore,
  confirmed-restore recovery, non-admin lock).

### PHASE 08 — Device Management / Discovery (2026-09-04)

Multi-device records with local observation state, plus read-only LAN
discovery that can never configure anything on its own. Verified against
SQLite, loopback sockets and the mock context; no real-device run.

#### Persistence (schema version 7)

- `devices.last_seen_at`: last successful contact, stamped by connection
  tests and successful syncs, `None` until a device answers. Forward-only
  migration 7, additive and resumable; v1-upgrade and preservation tests.

#### Protocol (`clockmanager.protocol.discovery`, PySide6-free)

- `probe_tcp` (TCP reachability only, no command sent), `hosts_from_cidr`
  (refuses ranges over 1024 addresses), `local_subnet_hosts`
  (best-effort, never raises), `scan_hosts` (parallel, input order kept),
  `identify_device` (connect, read snapshot, always disconnect — no write
  operation exists in the module, pinned by an AST test).
- Discovered devices carry identity only; nothing is stored, modified or
  deleted by any discovery function.

#### Services

- `DeviceService`: `record_last_seen` / `mark_seen`, `status` /
  `statuses` (last-seen plus stored counts and sync history, local reads
  only), `register_discovered` (the only discovery-to-profile path:
  operator-supplied name, duplicate-name and duplicate-address refusal,
  admin-gated, writes locally only), plus `probe_host` / `scan_network` /
  `identify` wrappers so the GUI never imports the protocol layer.
- Successful syncs stamp last-seen, so the status line reflects real contact.

#### GUI

- Device settings view shows last-seen/sync state for the selected profile
  and gains a read-only discovery group: check one address, scan the local
  network, select a result and register it with a name. Registration of an
  already-stored address is refused with a pointer to the existing profile.

#### Tests

- 18 discovery tests (loopback probe, CIDR expansion/limits, scan order and
  de-duplication, identify without writes with disconnect-on-failure, the
  no-write AST guard), 18 device-management tests (stamps, statuses,
  registration rules, migration 7), 6 GUI tests (state label, check without
  storing, explicit register, duplicate refusal, empty-LAN scan, canned
  scan select-and-register).

### PHASE 07 — Authentication / Roles / Audit (2026-09-04)

Local accounts and three roles separating admin/office access. Verified
against SQLite and the mock context; no real-device run.

#### Domain (`clockmanager.domain.auth`, PySide6-free)

- `Role` (`admin` / `office_staff` / `viewer`), `Permission` (accounts,
  device settings, device users, diagnostics, employees, sync, live,
  audit view, exports) and `ROLE_PERMISSIONS`: the one matrix both the
  service layer (refuse) and the GUI (hide/disable) decide from.
- `can` / `require` (`SecurityError`, no sensitive content in messages);
  reads need no permission — every role views dashboard, users,
  attendance, employees, timesheets and reports.

#### Security (`clockmanager.security.passwords`, stdlib only)

- Salted PBKDF2-HMAC-SHA256 (`pbkdf2-sha256$iterations$salt$hash`,
  210k iterations, 16-byte salt), constant-time verify, username/password
  validation. Plaintext exists only for one hash/verify call; hashes are
  excluded from `repr` and redacted like any password-named value.

#### Persistence (schema version 6)

- New `app_users` table (username unique, display name, role, salted hash,
  active flag, last login). Forward-only migration 6, additive and
  resumable; v1-upgrade tests plus the sensitive-column guard now allows
  `app_users.password_hash` alongside the device communication password,
  documented in `SECURITY.md`.
- New `AppUserRepository` (case-insensitive lookup, active-admin count
  for the last-admin guard).

#### Services (`AuthService`, via `context.auth`)

- First-run `bootstrap_admin`, `authenticate` (generic failure message,
  failures audited, never logs the password), `logout`, admin-only
  `create_user` / `list_users` / `set_role` / `set_active` /
  `change_password` (self-service proves the current password; admin
  resets do not need it). Refuses to demote/disable the last active
  admin. New audit actions `auth.login/logout/create_user/role_change/
  set_active/password_change`.
- `AuthSession` on the context holds the login; `context.audit` attributes
  to the logged-in username, else the OS account as before.
- Mutating methods on user/device/employee/sync/report services accept
  keyword-only `requester_role` and refuse roles without the permission;
  `None` keeps the legacy path (tests, background sync).

#### GUI

- Login dialog at startup, first-run admin setup, Logout back to login
  (one process serves consecutive operators, each session audited).
- Role-filtered navigation: Admin sees all views plus User accounts (ten at
  the time; PHASE 09 adds Backup as an eleventh, admin-only);
  Office staff hides Device settings/Diagnostics/User accounts; Viewer
  sees six read-only views. Hidden screens refuse via status message;
  Developer menu is admin-only. Mutating controls disable per role and
  the service refuses regardless.
- New User accounts view (admin): list, add, change role, enable/disable
  (confirmed), reset password. All password fields masked.

#### Tests

- 50 new tests (29 unit, 18 GUI, 3 migration). New suites: hashing (salt
  uniqueness, malformed/foreign hashes fail closed, no plaintext in hash), validation, full role
  matrix, account lifecycle (setup, case-insensitive login/unique
  create, failures audited without credentials, disable, last-admin
  guard, self/admin password change, logout), service refusals per
  role, v6 migration (fresh, v1 upgrade, resumable), no credential in
  storage/`repr`/audit, plus GUI smoke tests (dialogs, per-role
  navigation, per-view disabling, developer-menu gating, accounts view).
- ruff (lint + format) and mypy strict pass clean.

### PHASE 06 — Reports / Exports (2026-09-04)

Derived, read-only reporting over immutable stored attendance and the
append-only sync/audit history. Verified against SQLite and the mock
context; no real-device run.

#### Domain (`clockmanager.domain.reports`, PySide6-free)

- `ReportType` (daily attendance, employee timesheet, weekly, pay-period,
  exceptions, device activity, sync history, audit), `ReportFilter`
  (date range, employee, user ID, device, department, punch/status,
  exceptions-only, with inverted-date refusal) and `Report` (titled
  string table; rows validated against the columns).
- Dependency-free exporters: CSV via `csv`, JSON with metadata plus row
  dicts, minimal single-sheet XLSX via `zipfile` (inline strings, no new
  dependency), minimal Helvetica PDF via raw PDF objects. Columns are
  display-safe by construction: no communication password, no credential
  region, no event key.

#### Services (`ReportService`, via `context.reports`)

- All eight reports built from repository reads only; timesheet-based
  reports reuse `TimesheetService` so exception flags always agree with
  the Timesheets view. Weekly is Monday–Sunday regardless of pay cadence.
- New filtered reads: `AttendanceRepository.list_in_range`,
  `SyncHistoryRepository.list_filtered`, `AuditRepository.list_filtered`
  (reads only; the append-only guards still hold).
- `export()` returns `(bytes, filename, mime)` and audits every export as
  `report.export` (new `AuditAction`).

#### GUI

- New Reports view: report picker, start/end dates, employee picker,
  department filter, exceptions-only flag, Generate plus CSV/XLSX/PDF/JSON
  export via save dialog. All work off the UI thread; navigation grows to
  ten views.

#### Tests

- 613 tests. New suites: all eight reports, department/user/device/
  punch/status scoping, unknown-punch exceptions, CSV/JSON/XLSX/PDF
  round-trips (XLSX unzipped, PDF header), export auditing,
  no-secret columns, reports-never-mutate attendance, plus GUI smoke
  tests for the Reports view.
- ruff (lint + format) and mypy strict pass clean.

### PHASE 05 — Employees / Timesheets / Payroll (2026-09-04)

Business layer above raw attendance: employees, pay schedules and derived,
recalculable timesheets. Verified against SQLite and the mock context; no
real-device run.

#### Domain (`clockmanager.domain.payroll`, PySide6-free)

- `Employee`: canonical user ID, names, active flag, department, position,
  email (validated), notes.
- `PaySchedule` / `PayScheduleType`: weekly, bi-weekly, semi-monthly,
  monthly periods in an explicit IANA timezone; `pay_period_for` and
  `pay_periods_between` with boundary tests for every cadence.
- `TimesheetRules`: day-cutoff hour, duplicate interval, maximum shift,
  optional daily/weekly overtime thresholds, HH:MM/decimal display.
- `build_timesheet` / `summarise_day`: global chronological IN->OUT pairing
  (an overnight pair stays one shift attributed to the IN day and flagged
  `overnight`), duplicate flagging with exclusion, missing punches (trailing
  IN, leading OUT, unknown punch values — preserved, never paired),
  excessive shifts, daily/weekly/pay-period totals. Period overtime prefers
  the daily rule when both are set, so weekly cannot double count.
- `interpret_naive`: naive device-local wall time becomes aware in a named
  zone — never silently labelled UTC. Durations are real elapsed time taken
  in UTC: subtracting two aware datetimes that share one `ZoneInfo` object
  compares wall time and ignores DST transitions (pinned by spring-forward
  and fall-back tests: a 00:30-03:30 shift is 2h / 4h elapsed, not 3h).

#### Persistence (schema version 5)

- New `employees` table (canonical user ID unique), `employee_device_links`
  (one row per employee/device pair, cascade delete removes mappings but
  never the employee) and `pay_schedules` (cadence, anchor, timezone,
  cutoff, duplicate interval, max shift, display, overtime thresholds, one
  active). Timesheets have no table: they are derived on demand.
- Forward-only migration 5, additive only, resumable; v1-upgrade tests plus
  a guard that no credential/card/biometric column exists in the new tables.
- New `EmployeeRepository`, `PayScheduleRepository` (single-active
  enforcement), and `AttendanceRepository.list_for_users_in_range` for
  timesheet reads (raw rows never modified).

#### Services (via `context.employees`, `context.timesheets`)

- `EmployeeService`: create/update/(de)activate/link/unlink with duplicate
  user-ID refusal; every change audited (`employee.create/update/
  deactivate/reactivate/link`).
- `TimesheetService`: schedule administration plus `build` for one employee
  and period (resolves canonical plus linked user IDs, interprets wall time
  in the schedule zone) and `build_for_current_period`. Same-period rebuild
  after new punches returns new totals — proof of derived/recalculable.

#### GUI

- New Employees view: list, search, add/edit dialog (domain-validated),
  deactivate/reactivate; new Timesheets view: active-employee picker,
  current/previous period, per-day rows (first IN, last OUT, worked, OT,
  missing, flags) plus regular/overtime/total in the schedule's display
  format. Both work off the UI thread; navigation grows to nine views.

#### Dependencies

- `tzdata` added: Windows ships no IANA database, and pay-period/DST
  calculation needs `ZoneInfo` to resolve everywhere.

#### Tests

- 593 tests, 91% statement coverage. New suites: pay-period boundaries for
  all four cadences, daily flags (missing/duplicate/excessive/overnight/
  unknown punch), overtime precedence, DST spring-forward/fall-back elapsed
  time, schedule-timezone attribution, naive-input rejection, HH:MM/decimal
  formatting, employee CRUD/mapping/auditing, derived-timesheet builds
  (isolation between employees, alias IDs, recalculation, overtime
  schedule), v5 migration (fresh, v1 upgrade, resumability, no sensitive
  columns), and GUI smoke tests for both views.
- ruff (lint + format) and mypy strict pass clean.

### PHASE 04 — Attendance Synchronization (2026-09-04)

Reliable live + historical attendance sync with local storage, duplicate-safe
reconciliation and sync history. Verified against the mock device and SQLite;
no real-device sync has been run yet.

#### Sync core (`clockmanager.sync`, PySide6-free)

- `SyncSource`: `historical` / `manual` / `live` / `background` / `recovery`.
- `build_event_key`: SHA-256 over the device natural key, stable across the
  SQLite naive-datetime round trip (aware values normalise to UTC wall-clock
  seconds; microseconds dropped — no MB1 timestamp carries them).
- `plan_inserts`: pure reconciliation. Skips known event keys, keys seen
  within the batch, and natural keys for rows stored before keys existed.
  Unknown user IDs are planned with a `None` employee snapshot, never dropped.
- `status` is identity only, never interpreted. Direction derives from `punch`
  on display.

#### Persistence (schema version 4)

- `attendance_events` gains `received_at` (UTC store time), `source`,
  `event_key` and `employee_name` (display-name snapshot, `None` when the user
  is unknown). New unique index on `(device_id, event_key)` alongside the
  existing natural-key constraint.
- New append-only `sync_history` table: device (plain value, so removing a
  profile keeps its history), mode, source, seen/new/duplicate counts,
  outcome and error. No update or delete API.
- Forward-only migration backfills `received_at` from `created_at`,
  `source='historical'`, and deterministic keys for pre-existing rows;
  resumable and covered by v1-upgrade tests.
- New `AttendanceRepository` (key sets, savepoint-per-row `try_insert` so one
  duplicate cannot fail a whole sync, newest-first listing) and
  `SyncHistoryRepository`.

#### Service (`SyncService`, via `context.sync`)

- `initial_sync` / `incremental_sync` / `manual_sync` / `background_sync` /
  `recover`, plus `background_sync_if_due` against the profile's sync
  interval. Every run re-reads the whole device log (the MB1 has no
  incremental API) and behaves incrementally through duplicate detection.
- `record_live_event(s)`: stores live-capture punches duplicate-safe.
- Device failures return failed `SyncResult`s and are recorded in the history,
  never raised past the GUI. A run after a failure is automatically labelled
  `recovery`: the re-read picks up everything missed offline or missed by
  live capture.
- `received_at` normalised to aware UTC on read; `occurred_at` kept naive as
  device-local time by design.

#### GUI

- Attendance view shows locally stored punches (works offline) with Sync now
  and Refresh, a stored-count/IN/OUT/sync-state status line, and a
  user-or-name filter. New columns: Employee, Source.
- Live view stores each arriving punch locally (`live` source, duplicate-safe)
  with a Stored/Duplicate column; names are snapshotted once at capture start
  so per-event storage needs no extra device I/O.
- Background sync timer: every minute, runs `background_sync_if_due` off the
  UI thread for the first enabled profile (skipped while live capture owns
  the connection). Failures stay in the log/history, never pop up.

#### Tests

- 543 tests, 91% statement coverage. New suites: key stability and
  naive/aware equivalence, reconciliation (duplicates, in-batch duplicates,
  natural-key fallback, unknown UID, status handling), service tests for
  initial/repeated/incremental/manual sync, unknown-UID storage, live
  store + duplicate + missed-event recovery, failed-sync recording with
  automatic recovery flag, connect-failure-as-result, history ordering and
  survival across device removal, summaries, scheduling, UTC normalisation,
  and v3-row dedupe.
- Migration tests for v4 (fresh install, v1 upgrade, backfill, preservation,
  resumability).
- GUI tests updated for the stored-data views; thread guards extended so the
  attendance view still performs no I/O on the UI thread.

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
