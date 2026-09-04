# STATUS.md

## Current phase

PHASE 02 — Windows GUI / Settings / Diagnostics: **complete**.

## Next phase

PHASE 03 — User Management.

## What exists now

Python 3.12 package `clockmanager` under `src/`, installed editable
(`pip install -e .[gui,dev]`).

Layer separation is in place and enforced by tests:

- `clockmanager.domain` — `Privilege` (0 Employee / 14 Admin), `PunchDirection`
  (0 IN / 1 OUT), `DeviceIdentity`, `DeviceInfo`, `DeviceUser`,
  `AttendanceEvent`
- `clockmanager.persistence` — SQLAlchemy 2.0 ORM, SQLite engine, schema
  bootstrap, `DeviceRepository`
- `clockmanager.protocol` — read-only NG-MB1 core: `AttendanceDevice` interface,
  `NGTecoMB1Device`, `DeviceConnectionSettings`, 120-byte user parser,
  attendance and live-event parsers, capability model, retry/reconnect,
  structured exceptions, and `MockAttendanceDevice`
- `clockmanager.sync` — boundary only (PHASE 04)
- `clockmanager.services` — `bootstrap()`, `ApplicationContext`,
  `ApplicationStatus`
- `clockmanager.security` — redaction helpers
- `clockmanager.diagnostics` — structured JSON logging with a redacting filter
  on every handler
- `clockmanager.gui` — PySide6 application: navigation shell plus Dashboard,
  Users, Attendance, Live events, Device settings and Diagnostics views; the
  only subpackage allowed to import PySide6

Entry point `clockmanager` starts the GUI; `clockmanager --headless` runs the
same bootstrap without importing PySide6.

Set `CLOCKMANAGER_USE_MOCK_DEVICE=1` to run the whole application against the
built-in mock device with no hardware attached. The window says so in its
status bar.

Database schema version 2: `schema_info`, `devices`, `device_users`,
`attendance_events`. `devices` carries connection settings (host, port,
communication password, timeout, auto reconnect, sync interval, enabled).
Attendance carries a natural-key unique constraint (`device_id`, `user_id`,
`occurred_at`, `punch`, `status`) ready for PHASE 04 duplicate detection. No
user credential, card or biometric column exists.

A forward-only migration runner applies pending migrations one at a time,
recording each before the next begins, so an interrupted upgrade resumes.

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
- Generic pyzk user writing is not yet approved for MB1.
- PIN/card handling needs careful validation.
- Face/fingerprint template support remains investigation-gated.

## Known limitations of the current build

- **Nothing in PHASE 01 has been run against the real NG-MB1.** The adapter is
  covered by fixtures, a fake transport and a mock device only. The opt-in
  integration suite exists but has not been executed. Treat the adapter as
  unproven on hardware until it is.
- Attendance record size (8/16/40) on a real MB1 is still unconfirmed; the
  parser resolves it at runtime and refuses ambiguous payloads.
- The live-capture loop depends on pyzk's name-mangled `_ZK__sock` and
  `_ZK__ack_ok`. pyzk is pinned to `==0.9` because of this.
- `has_credential_data` reports only that the credential region holds non-zero
  bytes. That this always means "a PIN is set" is UNVERIFIED.
- No device write path exists. `write_users` and `clear_attendance` are marked
  UNSUPPORTED; `set_time`, `delete_users` and both biometric reads are marked
  UNVERIFIED and cannot be invoked. "Remove device" in the GUI deletes the
  local record only.
- **The device communication password is stored unencrypted at rest.** It is
  kept out of logs, `repr` and the GUI, but the data directory's OS permissions
  are the only control on the stored value. See `SECURITY.md`.
- Roles do not exist yet (PHASE 07), so diagnostic detail is gated on the
  `developer_mode` flag rather than on a user's role.
- `clockmanager.sync` is still an empty boundary.

- The GUI has no user management, no attendance synchronisation, no employees,
  timesheets, reports or exports. Those are PHASE 03 onwards.
- Attendance and users are read live from the device and displayed; nothing is
  stored locally yet. Local storage and reconciliation are PHASE 04.
- Windows packaging/installer is not started (PHASE 13).
- SQLite timestamps are stored as timezone-aware UTC on write, but SQLite
  returns naive datetimes on read. Callers must not assume tzinfo survives a
  round trip until this is addressed.

## Protocol discoveries (PHASE 01)

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

## Rules for updates

Keep this file concise.
Update it at the end of every coding session.
