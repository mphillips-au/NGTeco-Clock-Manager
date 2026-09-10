# CHANGELOG.md

## Unreleased

## 0.14.0 — 2026-09-10 (first GitHub release)

The first published build. Everything below this heading down to PHASE 00
ships in it; earlier entries were never released as a versioned build.

### Notification-area mode, single instance, GitHub releases (2026-09-10)

#### Running in the background

- **Close hides, Quit quits.** Closing the main window now hides it in the
  Windows notification area. Live capture and the periodic background sync
  keep running. The tray icon's menu has *Open* and *Quit*; clicking the
  icon restores the window; its tooltip says who is signed in and whether
  live capture is running. A one-time balloon explains where the window
  went. *File > Exit* and tray *Quit* stop capture and sync cleanly, as
  closing did before; *Logout* is unchanged.
- **Preference** in *Settings > General > Running in the background*, on by
  default, stored per Windows user in `QSettings` like the theme, applied
  to the open window immediately. Desktops without a notification area
  (and the offscreen test platform) keep the old close-to-quit behaviour.
- **Windows logoff/shutdown is never vetoed.** `commitDataRequest` lets the
  close through before Qt asks the windows to close.
- `LiveEventsView.capture_state_changed(bool)` is new; the tray listens to it.

#### One running copy per data folder

- Launching the application again (desktop shortcut, Start Menu, startup)
  brings the running window forward and exits. The second process never
  bootstraps, so it writes no database, log or audit entry.
- The lock is a Windows named mutex keyed on a hash of the data folder, so
  `--data-dir` still gives an independent instance and Windows accounts never
  collide. The hand-off travels over a `QLocalServer` pipe restricted to
  the same account (`UserAccessOption`), and the running copy acknowledges
  it. Without the acknowledgement a request sent while the running copy's
  UI thread was busy was lost; a test now holds the UI thread for 1.5 s to
  pin that.
- `AllowSetForegroundWindow` is granted by the launching process so Windows
  raises the window rather than just flashing its taskbar button.

#### Installer and releases

- The installer sets `AppMutex=NGTecoClockManagerRunning`, which every GUI
  process now holds, so Setup and the uninstaller refuse to replace files
  under a running copy. The message says to look in the notification area.
- `.github/workflows/release.yml`: pushing a `vX.Y.Z` tag runs ruff, mypy
  and the full test suite on a Windows runner, builds the executable and
  installer, runs the `--verify` smoke test, and publishes a GitHub Release
  with the installer and its SHA-256. *Run workflow* builds without
  publishing. The tag must match `__version__`.
- `packaging/build.py --all` and `--verify` now exit non-zero when the
  smoke test fails; previously the result was ignored.
- Version 0.13.0 → **0.14.0** in all four manifests.
- `PACKAGING.md` section 8 is now the GitHub release procedure; section 9
  documents the notification-area and single-instance behaviour.

#### Verified

- 18 new tests (`tests/gui/test_tray.py`, `tests/unit/test_windows.py`):
  hide versus close versus quit versus logout, session-end handling, tray
  menu and tooltip, the preference, the Settings toggle, and single-instance
  hand-off between **real separate processes**, including a full
  `python -m clockmanager` second launch that exits without creating the
  data folder.
- Full suite 1023 passed (22 real-device tests deselected as usual); ruff
  and mypy clean. `build.py --all` built and smoke-tested
  `NGTecoClockManager-Setup-0.14.0.exe` (42.5 MB) on this machine.
- **Frozen build on a real desktop:** a second launch of the release
  `clockmanager.exe` against the same data folder exited in about 1 s with
  code 0, leaving one process, and `NGTecoClockManagerRunning` was held.
  Qt reports a notification area with balloon support in this session.
- **Installer:** silent install (exit 0); re-running Setup while the
  installed app was running was refused (exit 1) and left the app running;
  after quitting, reinstall succeeded (exit 0); silent uninstall removed the
  program folder, Start Menu entry and uninstall key and left the data
  folder alone.
- Not verified by automation: clicking the tray icon and its menu on a real
  desktop. The offscreen test platform has no notification area, so those
  paths are covered only at the signal level.

### PHASE 17 — Web/API boundary over the headless core (2026-09-06)

A FastAPI REST boundary over the existing application services, built for
the future web frontend (PHASE 18). Verified against SQLite and the mock
context; no real-device run. Manually smoke-tested as a live server
(`--api-serve` + mock device: health, first-admin setup, login, device
create, OpenAPI serving 44 paths).

#### What it exposes

- **Auth** (`/api/auth/...`): first-run setup, login (generic failure
  message, failures audited), logout, `me`, the role/permission matrix.
  A successful login issues an opaque in-memory bearer token (24 h
  expiry, revoked on logout and on password change); passwords travel
  only on setup/login/password calls. Account administration
  (list/create/role/active/password) is admin-only.
- **Devices** (`/api/devices/...`): profile CRUD, local-only statuses,
  connection test, plus read-only discovery (probe/scan/identify) with
  `register_discovered` as the only storing path. `GET
  /api/devices/{id}/inspect` exposes the PHASE-15-wiring device
  information in one call — identity with capacity counters,
  allow-listed settings, fingerprint enrolment metadata (counts only,
  never templates) and the device operation log — read-only end to end,
  with unreadable sections degrading to notes. The communication
  password is write-only: responses carry only
  `has_communication_password`, and omitting it on update keeps the
  stored secret.
- **Device users** (`/api/devices/{id}/users...`): list, enrolment
  (fingerprint counts only, never templates), read-one, create/update,
  and delete behind `?confirmed=true`. The service refuses unless the
  installation unlocks device writing; every attempt is audited.
- **Attendance / sync** (`/api/attendance/...`,
  `/api/devices/{id}/sync`, `/api/sync/history`): stored reads that keep
  working with the clock down, and manual/initial/incremental/recovery
  sync triggers. A device failure arrives as a failed result, not a
  raise.
- **Live state** (`/api/live/status`, `/api/live/recent`): per-device
  stored/live-sourced counts plus last sync outcome, and stored
  live-capture punches newest-first. Stored state, never an open device
  socket: a browser refresh cannot miss a punch and no browser ever
  holds a device connection.
- **Employees / pay schedules / timesheets** (`/api/employees/...`,
  `/api/schedules/...`, `/api/timesheets...`): full employee lifecycle
  with device links, admin-gated schedule administration, and derived
  timesheet builds (one period, current period).
- **Reports / audit** (`/api/reports/{type}`, `.../export`,
  `/api/audit...`): all eight derived reports as JSON plus
  CSV/XLSX/PDF/JSON file exports (every export audited), and the
  append-only audit log behind `audit.view`.
- **System**: unauthenticated `GET /api/health` (counts only, for
  containers), authenticated `GET /api/status`, interactive docs at
  `/api/docs`. Error mapping: role refusal → 403, capability refusal →
  403, validation → 400, device failure → 502; no traceback or secret
  ever leaves the server.
- **CLI**: `clockmanager --api-serve` (uvicorn, `--api-host` /
  `--api-port`, default `127.0.0.1:8080`). `--serve` stays the PHASE-16
  headless sync loop; the two sit beside each other.

#### Architecture

No protocol logic is duplicated and the browser never touches TCP 4370:
every route calls an application service, pinned by a layering test
(the API may import only `protocol.errors` exception types for the HTTP
mapping, never the transport or parsers; persistence stays behind the
services too). New `api` extra (`fastapi`, `uvicorn`) in
`pyproject.toml`, also in `dev` with `httpx` for the tests. No database
migration: the API adds no tables.

#### Tests

`tests/unit/test_api.py` (24 tests, all against the mock device):
setup/login/logout/me, role matrix, admin-only accounts and device
profiles, secret-free device CRUD, device inspection sections with the
unknown-device refusal, locked-write refusal (403),
unlocked mock write round-trip (create → read-back → unconfirmed
refusal → confirmed delete), duplicate-safe sync with history, viewer
sync refusal, live status/recent shapes, employee lifecycle, payroll
gating with timesheet builds, all-reports JSON plus CSV export with the
missing-parameter refusal, and audit gating with secret-free entries.
Full suite: **951 passed** (22 real-device deselected), ruff and mypy
clean on every file this phase touched.

#### Known limitations

- Tokens live in process memory: a restart logs everyone out, and there
  is no login throttling or lockout (same as the GUI). Serve HTTPS in
  production — bearer tokens must not travel in cleartext past
  localhost.
- Device I/O runs synchronously in the server process; a slow clock
  holds one worker while it answers. Live-capture streaming
  (websocket/SSE) is a PHASE-18 decision; this phase exposes live
  *state*, not a live *feed*.
- Backup/restore and protocol diagnostics stay out of the web boundary
  for now: the former moves a database copy containing the stored
  device secret, the latter is admin/developer-mode tooling.

### PHASE 15 (wiring) — the proven capabilities, surfaced in the app (2026-09-06)

`phases/PHASE-15.md` proved four things on the real NG-MB1 that the
application then did not use. This wires all four through the layers —
protocol, capability model, services, GUI and diagnostics — and adds
nothing that PHASE 15 did not establish on hardware. No new write of any
kind; every operation added here is a read.

#### Device settings (`CMD_OPTIONS_RRQ`)

- `NGTecoMB1Device.read_device_options()` reads the device's own settings
  and reports a refusal (code 4999) as "not available on this model"
  rather than as an error, because "this firmware has no work codes" is a
  useful answer.
- The names it will ask for are a fixed allow-list,
  `clockmanager.protocol.options.NG_MB1_OPTIONS`, each carrying the caveat
  its value needs. `IPAddress` ships with the warning PHASE 15 earned:
  the device's stored address is not the address it answers on, and it
  must never be used to reconnect or to scan.
- A name matching a credential-shaped fragment is refused before a request
  is built, so `ComKey` and its relatives cannot reach a panel, an export
  or a log.
- **There is no option write.** `Capability.WRITE_DEVICE_OPTIONS` is
  UNSUPPORTED, and an unsupported capability cannot be operator-unlocked.

#### Capacity and usage (`CMD_GET_FREE_SIZES`)

- `DeviceInfo` now carries `DeviceStorage`, so every existing snapshot —
  dashboard connection test, diagnostics, device information — reads "6 of
  30,000 used — 29,994 free" instead of "6".
- A count the device did not report stays "Not reported"; it is never
  defaulted to zero. pyzk's `cards` field is deliberately absent: nothing
  establishes what it counts.

#### Fingerprint enrolment, per user

- The Users screen shows how many fingers each person has enrolled,
  matched to their record by device UID, with a "Cannot clock in" filter
  for users holding neither a PIN nor a fingerprint.
- "Unknown" and "None" are different words there and are never conflated:
  a device that would not enumerate has said nothing, not "nobody is
  enrolled". A failed enumeration costs the fingerprint column, never the
  user list.
- No template byte leaves the protocol parser; only a template's length is
  ever reported.

#### The device's own operation log (`FCT_OPLOG`)

- Decoded and shown: the clock's record of keypad activity, which is a
  different question from the audit trail's record of what this
  application did.
- Only the 16-byte record size and the timestamp at bytes 4:8 are proven,
  so operation codes display as numbers ("Operation 5"), never as invented
  names, and an undecodable timestamp shows as "Unreadable timestamp"
  instead of taking the other records down with it.

#### Elsewhere

- New read-only `InspectableDevice` interface, implemented by the adapter
  and the mock, so a device that cannot describe itself is a type error
  rather than a runtime surprise.
- `DeviceService.inspect()` gathers all of it in one connection; a section
  the device will not answer becomes a note, and the rest still arrives.
- Settings ▸ Device settings gains a read-only "Device information" tab
  (capacity, settings, fingerprints, device log).
- The diagnostics trace and its export gained storage, settings and
  operation-log sections. `CMD_DB_RRQ` payloads stay withheld whole from
  previews, which is what keeps template bytes out of the export.
- The write-availability text no longer says no MB1 has accepted a record:
  one has. Writing still ships off — proving a device accepts a record is
  not permission for an installation to send one.

Full suite: **1001 passed** (22 real-device deselected), ruff and mypy
clean. `tests/unit/test_phase15_wiring.py` adds 49 tests pinning each
capability, its gate, and the limits above.

#### Not done, deliberately

- Nothing that PHASE 15 listed as a trap: no card read or write, no
  fingerprint enrolment or template upload, no face work, no `set_time`,
  no attendance clear, no option write.
- Operation-log codes are not named, and `status` is still not interpreted
  as a verification method. Both need a deliberate action at the device
  that nobody has performed yet.

### PHASE 16 — Synology / Linux headless service (2026-09-06)

The proven device/sync core runs without the Windows GUI. Verified against
SQLite and the mock context; no real-device run, and the Docker image has
not been run on Synology hardware.

#### Service (`clockmanager.headless`, PySide6-free)

- `HeadlessService.run_once()` reconciles every enabled, configured device
  through `SyncService.background_sync_if_due` — the exact code the GUI
  uses — so the 120-byte parser, attendance engine, live capture,
  reconciliation and persistence are reused, never duplicated (a test
  pins that the package never imports the transport or parsers).
- Reconnect: failed syncs are results, not exceptions. Consecutive
  failures back off 30 s doubling to a 10-minute cap and never give up;
  unexpected exceptions are logged without stopping the pass.
- Optional live-capture workers (off by default), one thread per device,
  storing punches duplicate-safe with a one-time name snapshot; shutdown
  is cooperative via SIGTERM/SIGINT.
- Stdlib-only health endpoint: `GET /health` (liveness) and `GET /ready`
  (200 after the first pass, 503 until then), carrying names/counts/
  timestamps only — no communication password, PIN, card or biometric
  value by construction.
- CLI: `clockmanager --serve` (loop until signal) and `--serve-once`
  (one pass, cron-friendly), plus `--interval`, `--health-bind`,
  `--live` / `--no-live` overrides. New `service_poll_seconds`,
  `service_health_bind` and `service_live_capture` settings, each with a
  `CLOCKMANAGER_SERVICE_*` environment variable. Start/stop audited as
  `service.start` / `service.stop`.
- Docker: `Dockerfile` (python:3.12-slim, no GUI dependencies, non-root,
  `/data` volume, `HEALTHCHECK` on `/health`) and a Synology Container
  Manager compatible `docker-compose.yml`.

#### Integration note

A parallel session added a second `--serve` flag for the PHASE-17 API,
breaking the argument parser; it is now `--api-serve` (`--api-host` /
`--api-port` unchanged, no test referenced them). `--serve` stays the
headless sync loop.

#### Tests

`tests/unit/test_headless_service.py` (37 tests): configuration
round-trips and refusals, backoff growth and cap, bind parsing, sync-now
/ not-due / skip-disabled / failure-backoff / exception-survival passes,
health/ready lifecycle over real HTTP, secret-free snapshots, loop
shutdown with lifecycle audit, live-worker store and stop, `--serve-once`
CLI, and the no-duplicated-protocol AST guard. Layering tests now cover
`clockmanager.headless`. Full suite: **916 passed**, ruff and mypy clean
on every file this phase touched (the tree carries pre-existing ruff
failures in the parallel session's uncommitted `api/` work).

### PHASE 15 — Capability investigation against the real NG-MB1 (2026-09-06)

The first session to write to the real device (serial NBF6260700048). An
investigation, not a feature: the full report, per-capability evidence map,
prioritised roadmap and incident write-up are in `phases/PHASE-15.md`.

#### Incident — read this first

A user record written with a **13-character user ID**, against the device's own
`~PIN2Width=9`, was accepted and read back intact. Afterwards the device lost
**both enrolled fingerprint templates** (`fingers` 2 -> 0, faces and PINs
unaffected), that record became undeletable, and the ZK service stopped
completing sessions for about forty minutes while TCP 4370 stayed open. The
device recovered on its own and was rebooted with `CMD_RESTART`.

Attribution is **not established** — two well-formed deletes happened between
the long-ID write and the first observation of `fingers=0`. The long user ID is
the only out-of-spec operation in the sequence and the only record that became
unmanageable.

Left for the operator, physically: re-enrol both fingerprints, and delete
`ZZTEST-LONGID` at UID 901 from the keypad. Both real users' records were
confirmed **byte-identical** to their pre-write snapshots, and attendance is
unchanged at 6 records.

#### Proven on hardware

- **The write path works.** `CMD_USER_WRQ` + `CMD_REFRESHDATA`: create, rename,
  privilege 0 -> 14 -> 0, PIN set, rename with the PIN preserved, PIN clear,
  and delete, each verified by read-back on disposable `ZZTEST-` accounts.
- **The PIN is at bytes 3:11**, NUL-padded ASCII. `has_credential_data` really
  does mean "a PIN is set", proven in both directions.
- **The device accepts an application-chosen UID** (UID 900, above its 200-user
  capacity, stored and deleted cleanly).
- **Fingerprints can be enumerated**: `CMD_DB_RRQ`/`FCT_FINGERTMP`, entries
  framed `<HHbb`, and the UIDs match the 120-byte user records exactly.
- **Writable field budgets are smaller than the record regions**: user ID 9
  bytes, last name 23. A 30-character last name came back truncated to 23.
- **The 40-byte attendance record's leading uint16 is a record index**, now
  corroborated across two users (index 6 belongs to user "2").
- A large read-only device-option surface (`CMD_OPTIONS_RRQ`) and the device's
  own 33-record operation log (`FCT_OPLOG`) exist and are untouched.

#### The defect that hid all of it

`_compare_records` demanded byte equality outside the credential region, so
**every successful write failed verification**. The MB1 does not store the bytes
it is given: it sets byte 87 itself, and it does not zero-fill field tails, so
records carry residue from previous occupants (the live UID 2 record reads
`"Stilo\0nis"` — the tail of "Gianginis"). Verification now compares decoded
fields plus credential presence, and was re-run end to end on hardware.

#### Capability model

- New `Support.OPERATOR_LOCKED` and `DeviceCapabilities.locked()`, separating
  *the device supports this* from *this installation may do it*. Without it,
  graduating `WRITE_USERS` to `SUPPORTED` would have switched writing on
  everywhere — the capability state had been doubling as the safety gate.
- `WRITE_USERS`, `DELETE_USERS`, `WRITE_USER_PASSWORD` and `READ_FINGERPRINT`
  (enumeration only) -> `SUPPORTED`, each carrying its hardware evidence.
- `SET_TIME` and `READ_FACE` stay `UNVERIFIED`; `WRITE_USER_CARD` and
  `CLEAR_ATTENDANCE` stay `UNSUPPORTED`.

#### Code

- `src/clockmanager/protocol/mb1.py`: field-semantic `_compare_records`; new
  `read_fingerprint_slots()`; live-event body size logged so the next real
  punch settles which layout this firmware sends.
- `src/clockmanager/protocol/records.py`: `parse_fingerprint_payload`, which
  discards template bytes at the parser boundary.
- `src/clockmanager/protocol/builders.py`: refuses an over-long user ID or last
  name, in the builder as well as the domain layer.
- `src/clockmanager/protocol/constants.py`: `USER_PASSWORD_SLICE` (no longer a
  candidate), `USER_ID_WRITABLE_BYTES`, `USER_LAST_NAME_WRITABLE_BYTES`,
  `USER_DEVICE_FLAG_OFFSETS`, `CMD_DB_RRQ`, `FCT_FINGERTMP`.
- `src/clockmanager/protocol/trace.py`: **`CMD_DB_RRQ` payloads are withheld
  whole.** Wiring the fingerprint read into diagnostics without this would have
  hex-dumped real biometric templates into the trace and its export.
- `src/clockmanager/domain/models.py`: `FingerprintSlot` — four integers, no
  bytes.
- `src/clockmanager/domain/users.py`: `LAST_NAME_MAX_BYTES` 37 -> 23,
  `USER_ID_MAX_BYTES` 24 -> 9.
- `src/clockmanager/services/diagnostics.py`: a fingerprint-slot step in the
  protocol trace, reporting metadata only.

#### Tests

`tests/unit/test_phase15_capabilities.py` (37 tests): every capability
graduation, both field budgets, the read-back comparison's tolerance *and* its
strictness, the fingerprint entry framing and UID mapping, and the fact that no
template byte can reach a preview or a trace step. Existing capability tests
updated for `OPERATOR_LOCKED`. Full suite: **875 passed**, ruff and mypy clean.

#### Not determined

The card offset (no card available), what `status` means (nobody on site to
badge), which live-event layout this firmware sends, what bytes 90 / 83:87 /
11:35 hold, whether `read_sizes().cards` counts cards, what actually destroyed
the fingerprints, and how to delete UID 901 over the protocol. Each is listed in
`phases/PHASE-15.md` with what it would take.

### PHASE 14 (QA) — Production QA against the real NG-MB1 (2026-09-04)

> Shares a number with "PHASE 14 — Windows Packaging" below: two sessions
> ran in parallel and both claimed 14. This entry is the
> `phases/PHASE-14.md` brief; that one is `phases/PHASE-13.md`.

The first session to run this application against the real NG-MB1 (serial
NBF6260700048, ZMM510_TFT, Ver 8.0.4.5-7108-02). Hardening only; no feature
was added and no user, credential or attendance record on the device was
written, cleared or modified. Everything that touches the device here is a
read.

#### Defects found on hardware and fixed

- **A 40-byte attendance record's leading uint16 is the record's own index,
  not the user's device UID.** The MB1 uses the 40-byte form, so every punch
  this application had ever read carried the wrong value: three consecutive
  punches by the single enrolled user (device UID 1) stored `device_uid`
  1, 2 and 3, and the Attendance view showed them in a column labelled "UID".
  The parser now reports `device_uid=None` for this form. It also refuses a
  record whose user-ID text is empty instead of falling back to the index,
  which would have invented a user that is not enrolled. The 8-byte form,
  where the field really is a UID, is unchanged. Duplicate detection was never
  affected: the event key covers device, user ID, timestamp, punch and status,
  not `device_uid`.
- **The diagnostics trace silently corrupted attendance packets.**
  `redact_payload_preview` decided what a payload was from its length, and a
  120-byte body is both "three 40-byte attendance records" and "one 120-byte
  user record" — the ordinary case on this device. It zeroed bytes 3:35 as if
  they were a credential region, destroying the first punch's user ID, status,
  timestamp and direction in the trace an engineer reads to diagnose exactly
  that. The command code now decides; the shape is consulted only when the
  command is unknown, and then still errs towards redacting. User data of an
  unrecognised shape is now withheld rather than falling through to an
  unredacted dump — the one path that could have leaked a credential.
- **`clockmanager --help` crashed on a stock Windows console.** argparse wrote
  an em dash to a cp437/cp850 code page and Python raised
  `UnicodeEncodeError`, so a fresh install could not print its own help or
  pipe its output. `main()` now reconfigures stdout/stderr to replace
  unencodable characters before argparse can write anything.
- **Corrupt timestamps became real-looking punches.** Neither packed encoding
  has an invalid representation, so a garbled packet decoded to a valid date
  (`0xffffffff` → the year 2133) that would be stored, totalled into a pay
  period and reported. Both decoders now bound the year to 2000-2099 — the
  century the epoch-2000 encodings can meaningfully describe — and refuse the
  rest.

#### Schema

- Schema **8**: clears `attendance_events.device_uid`. Sync only inserts what
  is new and never rewrites a stored punch, so the record indices already on
  disk would have stayed on screen forever. The column is display-only, so no
  identity, punch or history is lost; affected rows simply show a blank UID,
  which is what a 40-byte read now records.

#### Verified against the real device

Discovery (a /24 scan found the clock and identified it safely) · connect
155 ms / clock read 5 ms / reconnect 156 ms · the 120-byte user parse with
privilege 14 mapped to Admin · 40-byte attendance records with punch 0 = IN
and 1 = OUT and `status` 1 and 15 preserved verbatim · sync idempotence
(3 new, then 0, then 0) · a real badged live event stored with source `live` ·
**live recovery** (the following full sync read 4 records and inserted 0, so a
punch captured live is not duplicated when the log is re-read) · a full
protocol trace whose TX/RX, sanitized export and log file contained no byte of
the device's non-empty credential region · offline behaviour (an unreachable
device fails as a result, is recorded in the history, leaves every local view
working, and is picked up by the next successful sync).

Verified alongside, against local storage: migrations forward from every
schema 1-7 and refusal of a newer one · backup create/preview/restore with
confirmation required, invalid archives refused and portable exports carrying
no credential · all three roles refused at the service layer for every
mutation they lack · every refusal and failure audited · all eight reports in
all four export formats · timesheets across both Australian DST transitions
(9 h and 7 h for the same 22:00-06:00 wall-clock shift, attributed to the IN
day) · malformed user, attendance, timestamp and live-event payloads refused
rather than guessed at · the 120-byte builder's validation, and the standing
guarantee that pyzk's 72-byte `set_user()` is never called.

#### Packaging and installer

The Windows packaging work landed mid-session, so the installer was tested
against these changes rather than deferred:

- Release PyInstaller build and Inno Setup installer both compile.
- The frozen executable survives cp437/cp850/cp1252 for `--help`,
  `--version`, `--headless` and the packaging session's own
  `--firewall-info`, which carries the same em dash that used to crash the
  CLI. That fix protects the new packaging commands too.
- **Upgrade without data loss**: a schema-7 database left by a previous
  install was opened by the packaged `clockmanager.exe`, migrated to 8 in
  place, and kept every device, employee, punch and event key. Only the
  bogus `device_uid` indices were cleared, which is the point of migration 8.
- Silent install -> launch -> silent uninstall: the program directory and
  Start Menu shortcuts are removed and `%LOCALAPPDATA%\NGTecoClockManager`
  (database, logs, backups) is left untouched.
- `pyzk` and the whole `clockmanager.protocol` package, including the fixes
  above, are confirmed present inside the installed executable's archive, so
  the device path ships complete.

#### Not tested

The **write path** (create/update/delete/PIN) was deliberately not exercised.
It stays off by default and UNVERIFIED; proving it needs a disposable
`ZZTEST-` account and the opt-in write suite.

A device sync **driven from the installed GUI** was not performed: the frozen
executable exposes no sync command, so the device work was done against the
same code from source. The bundle contents were verified instead.
### PHASE 14 — Windows Packaging (2026-09-05)

Production Windows packaging: PyInstaller builds, an Inno Setup 6
installer, versioning strategy and the release procedure. Full detail in
`PACKAGING.md`; summary in `STATUS.md` under "Windows packaging (PHASE
14)".

- `packaging/build.py`: unified build automation (`--assets`, `--dev`,
  `--release`, `--installer`, `--verify`, `--all`, `--clean`).
- `packaging/clockmanager.spec`: PyInstaller `onedir` spec producing a
  console development build (`clockmanager-dev.exe`) and a windowed
  release build (`clockmanager.exe`) with embedded PE version info and
  icon.
- `packaging/installer.iss`: Inno Setup 6 script — per-user install
  (`PrivilegesRequired=lowest`), Start Menu and optional desktop
  shortcuts, optional launch-at-Windows-startup task, clean uninstall
  that never touches the user data directory.
- `packaging/generate_assets.py` + `packaging/assets/`: generated
  application icon (`.ico`/`.png`).
- `src/clockmanager/windows.py`: new stdlib-only Windows platform module
  — HKCU `Run` key startup registration and firewall/network guidance
  (TCP/UDP 4370). No PySide6 import, preserving GUI-import layering
  (`tests/test_layering.py` extended accordingly).
- `src/clockmanager/__main__.py`: new `--enable-startup`,
  `--disable-startup`, `--status-startup` and `--firewall-info` CLI
  flags.
- `src/clockmanager/gui/app.py`: application window icon resolution
  (works both from source and from a PyInstaller bundle).
- Version bumped to `0.13.0` across `pyproject.toml`,
  `src/clockmanager/__init__.py`, `packaging/installer.iss` and
  `packaging/version_info.txt`; consistency enforced by
  `tests/unit/test_packaging.py`.

#### Tests

- `tests/unit/test_packaging.py` (build artefact / version consistency
  checks) and `tests/unit/test_windows.py` (startup registration and
  firewall guidance) — full suite: 804 passed, 17 deselected (`ruff`,
  `mypy` clean).

#### Verified this session

- `python packaging/build.py --release` compiles successfully.
- `python packaging/build.py --verify` passes against the compiled
  `dist\clockmanager\clockmanager.exe`: `--version`, `--firewall-info`
  and `--headless` bootstrap (real config/DB/log paths under
  `%LOCALAPPDATA%\NGTecoClockManager`, schema 7) all succeed.
- `python packaging/build.py --installer` compiled
  `NGTecoClockManager-Setup-0.13.0.exe` using Inno Setup 6.
- Full install lifecycle exercised end-to-end on this machine: silent
  install (`/VERYSILENT /SUPPRESSMSGBOXES`), Start Menu shortcut
  creation, `--version`/`--headless` against the installed executable,
  then silent uninstall — confirmed the program directory and Start Menu
  shortcuts are removed while `%LOCALAPPDATA%\NGTecoClockManager`
  (database, logs, backups) is left completely untouched.

#### Not verified this session

- No real NG-MB1 hardware was available, so the device-connection step of
  the release checklist was not exercised against real hardware in this
  session (consistent with every prior phase).

### PHASE 13 — Settings hub (2026-09-05)

Device settings, diagnostics and account administration stop being three
separate navigation entries and become sections of one Settings screen, as
the PHASE 12 brief asked. The sections are General, Device, Payroll,
Security and Developer.

#### Navigation

- Ten navigation entries instead of twelve: Dashboard, Users, Attendance,
  Live events, Employees, Timesheets, Reports, Audit log, Backup, Settings.
- Backup stays a top-level entry: restoring a database is an operational
  task, not a setting, and it remains administrator-only.
- `File ▸ Settings` opens the hub; `Developer ▸ Developer settings` opens it
  at the Developer section, under the same developer-mode gate as before.
- `MainWindow.show_settings_section(name)` opens one section and reports on
  the status bar when the role does not have it.

#### Role separation (`sections_for`)

A section a role may not use is not added at all, so there is no tab to
find:

- Administrator: General, Device, Payroll, Security, Developer.
- Office staff and viewers: General and Payroll only, with Payroll
  read-only. Developer tools remain invisible to office staff.

#### Payroll

`TimesheetService` already had full pay-schedule administration with no way
to reach it: the application only ever called `ensure_default_schedule()`.
The Payroll section surfaces it — the stored schedules, which one is active,
and a form to add one (type, anchor date, time zone, day cutoff, duplicate
interval, longest shift, daily/weekly overtime, decimal display).

- New `Permission.MANAGE_PAYROLL`, granted to administrators only.
  Deliberately separate from `MANAGE_EMPLOYEES`: office staff maintain the
  people, but overtime thresholds and pay periods decide what is owed.
- `create_schedule()` and `activate_schedule()` now take `requester_role`
  and enforce it, matching every other service; `None` keeps the legacy
  path for callers without an interactive identity.
- Changing the active schedule is confirmed, and says plainly that stored
  punches are not changed.

#### Other sections

- General: light/dark appearance applied immediately, and where this
  installation keeps its data, config, database and log.
- Security: hosts the accounts view, plus a read-only statement of what this
  build may write (device users, PINs, developer mode, database path) and a
  note that those come from the configuration file, not the application.
- Developer: hosts diagnostics plus the database metadata that was
  previously only reachable from the Developer menu.

#### Fixes

- The stylesheet overrode check-box and radio indicator sizes, which
  discarded the artwork the platform style draws: radio buttons rendered as
  blank space. The override is gone.
- Settings has a navigation icon.

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
