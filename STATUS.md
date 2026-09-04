# STATUS.md

## Current phase

PHASE 03 — MB1 User Management: **complete**.

## Next phase

PHASE 04 — Attendance synchronisation.

## What exists now

Python 3.12 package `clockmanager` under `src/`, installed editable
(`pip install -e .[gui,dev]`).

Layer separation is in place and enforced by tests:

- `clockmanager.domain` — `Privilege` (0 Employee / 14 Admin), `PunchDirection`
  (0 IN / 1 OUT), `DeviceIdentity`, `DeviceInfo`, `DeviceUser`,
  `AttendanceEvent`, plus user-management rules: `UserDraft`,
  `CredentialAction`, `UserChange`, `UserWriteOutcome`, `describe_changes`
- `clockmanager.persistence` — SQLAlchemy 2.0 ORM, SQLite engine, forward-only
  migrations, `DeviceRepository`, append-only `AuditRepository`
- `clockmanager.protocol` — NG-MB1 core: `AttendanceDevice` and
  `WritableUserDevice` interfaces, `NGTecoMB1Device`,
  `DeviceConnectionSettings`, the 120-byte user parser **and builder**,
  attendance and live-event parsers, capability model, retry/reconnect,
  structured exceptions, and `MockAttendanceDevice`
- `clockmanager.sync` — boundary only (PHASE 04)
- `clockmanager.services` — `bootstrap()`, `ApplicationContext`,
  `ApplicationStatus`, `DeviceService`, `UserService`, `AuditService`,
  `MockDeviceFactory`
- `clockmanager.security` — redaction helpers
- `clockmanager.diagnostics` — structured JSON logging with a redacting filter
  on every handler
- `clockmanager.gui` — PySide6 application: navigation shell plus Dashboard,
  Users, Attendance, Live events, Device settings, Audit log and Diagnostics
  views; the only subpackage allowed to import PySide6

Entry point `clockmanager` starts the GUI; `clockmanager --headless` runs the
same bootstrap without importing PySide6.

Set `CLOCKMANAGER_USE_MOCK_DEVICE=1` to run the whole application against the
built-in mock device with no hardware attached. The mock now keeps its contents
for the life of the application context, so the write path can be exercised end
to end without a clock.

Database schema version 3: `schema_info`, `devices`, `device_users`,
`attendance_events`, `audit_events`. No user credential, card or biometric
column exists in any of them.

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
- Face/fingerprint template support remains investigation-gated.
- No card field has been identified; card writing is UNSUPPORTED and cannot be
  unlocked.

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
- The audit actor is the operating-system account. Roles do not exist yet
  (PHASE 07), so diagnostic detail is still gated on the `developer_mode` flag
  rather than on a user's role, and the audit log is visible to anyone who can
  open the application.
- `clockmanager.sync` is still an empty boundary.
- Attendance is read live from the device and displayed; nothing is stored
  locally yet. Local storage and reconciliation are PHASE 04.
- Employees, timesheets, reports and exports do not exist. Windows
  packaging/installer is not started (PHASE 13).
- SQLite timestamps are stored as timezone-aware UTC on write, but SQLite
  returns naive datetimes on read. Callers must not assume tzinfo survives a
  round trip until this is addressed.

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
