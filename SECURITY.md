# Security

## Sensitive information

Never log:
- PINs/passwords
- card numbers/identifiers
- biometric template contents
- application secrets
- device communication secrets

## UI

Mask sensitive values.

Restrict developer/diagnostic views to administrators.

Admin sees every view including User accounts. Office Staff sees the
office workflows (Users, Attendance, Live events, Employees, Timesheets,
Reports, Audit log) but no Device settings, Diagnostics or User accounts.
Viewer sees six read-only views (Dashboard, Users, Attendance, Employees,
Timesheets, Reports); sync, live capture and every other mutation is
disabled, hidden screens refuse via a status message, and the service
layer refuses regardless of what the widgets show.

Office Staff must not see:
- protocol tools
- raw packet data
- credential data
- destructive device settings

## Local accounts and roles (PHASE 07)

Three local roles separate admin/office access: Admin, Office staff,
Viewer. The matrix lives in `clockmanager.domain.auth` and is the single
source both the service layer (which refuses) and the GUI (which
hides/disables) decide from.

Passwords are stored only as salted PBKDF2-HMAC-SHA256 hashes (stdlib,
210k iterations, 16-byte random salt per account). Plaintext exists only
for the duration of one hash or verify call. Hashes are excluded from
`repr`, redacted by the logging filter like any password-named value, and
never written to the audit log: login and account events record usernames
and actions only, including failures.

Rules enforced by `AuthService`: first run creates the initial admin;
thereafter account administration needs an admin; a login with a wrong
password or unknown name fails with one generic message; disabled
accounts cannot log in; the last active admin cannot be demoted or
disabled; changing your own password proves the current one, while an
admin reset does not need it (and never learns it — it was never stored).

Open items: no login throttling or lockout (failures are audited, guessing
is not slowed); the headless CLI performs no login, so local accounts
protect the GUI while OS permissions on the data directory remain the
boundary for the database file itself.

## Device writes

Every write:
- validates input
- identifies target device
- identifies target user
- requires confirmation where destructive
- reads back the result
- records an audit event

Implemented in PHASE 03. The byte-level sequence lives in the device adapter so
a caller cannot perform it partially; the policy around it (whether writing is
permitted, what the operator is shown, what is audited) lives in
`clockmanager.services.users`.

Device writing is **off by default** and is enabled per installation with
`CLOCKMANAGER_ENABLE_DEVICE_WRITES=1`. Writing a PIN needs the further
`CLOCKMANAGER_ENABLE_CREDENTIAL_WRITES=1`, because the credential region's
layout is unverified.

## Biometric data (PHASE 15)

The fingerprint store can be **enumerated** on the real device. Only metadata
crosses the protocol boundary: `FingerprintSlot` carries the user's device UID,
the finger index, the valid flag and the template **length** -- four integers
and no bytes. Template contents are discarded inside
`parse_fingerprint_payload` and are never returned, logged, exported or
persisted.

Two consequences are enforced rather than trusted:

- **`CMD_DB_RRQ` payloads are withheld whole from protocol traces.** The table
  read can return the fingerprint store, and the function selector that would
  say which table it was is not part of the payload, so a trace reports the
  size and nothing else. Adding the fingerprint read to diagnostics without
  this would have hex-dumped real templates into the trace and its export.
- **No template read, upload, delete or enrolment operation exists** in the
  adapter or the interface, and a test asserts that the only fingerprint method
  on a device class is the enumeration one.

Face templates remain unreadable: no command is known.

## User credential data

No user PIN, card identifier or biometric template is persisted anywhere.

The PIN's location in the record is now known (bytes 3:11, ASCII). That changes
nothing about how it is handled: the region is still confined to
`clockmanager.protocol`, still excluded from `repr`, still never decoded for
display, and the domain `DeviceUser` still carries only `has_credential_data`.
Knowing where a secret lives is not a reason to start reading it.

A PIN supplied by an operator exists only for the duration of one write:

- it is carried in `UserDraft.password`, excluded from `repr` so it cannot
  reach a log or traceback
- the raw 120-byte record, which contains the credential region, is confined to
  `clockmanager.protocol`; `RawUserRecord.raw` is excluded from `repr` and the
  type is never returned to a service or the GUI
- the domain `DeviceUser` has nowhere to put credential bytes; it carries only
  `has_credential_data`
- the GUI never populates the PIN field from the device, and masks it
- a change is described as an action ("Set a new PIN"), never as a value

## Audit log

`audit_events` is append-only. `AuditRepository` exposes `add`, `recent` and
`count` and no way to edit or delete a row, and the Audit log view is read-only.

Entries record refusals and failures as well as successes: a log that shows
only what succeeded cannot answer the question it exists for.

Rows are kept when a device profile is removed — `device_id` is a plain value,
not a foreign key — so deleting a device cannot erase the record of what was
done to it.

Every `detail` passes through `redact_text` before storage, and no
credential-shaped column exists on the table.

Actor identity is the logged-in username, falling back to the
operating-system account when nobody is logged in (headless CLI,
pre-login). Login, logout, account creation, role changes,
enable/disable and password changes are all audited; failures too.

## Device communication password

The device communication password is stored in the application database so the
application can reconnect without prompting.

Controls in place:
- excluded from `repr`, so it cannot reach a log or traceback
- never written to a log, a status message or an export
- masked in the GUI, and never echoed back into the settings form once saved
  (an empty field means "leave the saved password unchanged")

Open item: it is stored **unencrypted at rest**. The OS permissions on the
per-user data directory are the only control on the stored value. Encrypting it
(for example with Windows DPAPI) is outstanding and must be resolved before any
deployment where the data directory is not trusted.

This is a device connection secret set by an operator. It is not user
credential data: no user PIN, card identifier or biometric template is
persisted anywhere.

## Backups

Do not expose credentials in ordinary exports.
Document any backup containing sensitive device state.

A PHASE 09 backup zip contains the full database copy, so it holds the
stored device communication passwords alongside everything else
(`SECURITY.md`: unencrypted at rest). Protect a backup file like the data
directory itself; the manifest says so inside every zip.

The portable exports in the same zip (employees CSV/JSON, device-users
JSON, attendance CSV, audit CSV, sync history JSON) never contain a
communication password, a PIN, a card identifier or a biometric template:
device users export only the `has_credential_data` indicator. A test pins
the exact exported keys.

Backup creation and restore are administrator-only (`MANAGE_DEVICE_SETTINGS`),
in the service layer as well as in the GUI: the Backup view is hidden from
office staff and viewers. Every creation is audited as `backup.create`;
every restore is audited as `backup.restore` in the restored database, next
to the automatic pre-restore safety backup that preserves the replaced
state. A restore needs a valid preview *and* an explicit confirmation —
without confirmation nothing changes.

## Future web

The browser must never connect directly to TCP 4370.
The headless service is the network boundary.

## Logging

Use structured logs with redaction.
Protocol debugging can expose raw packets only in an explicit admin/developer mode and must redact known secret regions.

## Diagnostics exports (PHASE 10)

Protocol traces and their JSON exports are administrator-only: the
Diagnostics view is hidden from office staff and viewers, and the service
refuses non-admin roles regardless of what the widgets show.

Raw user records reach the export with the credential region zeroed inside
the protocol layer (`protocol/trace.py` refuses to redact buffers that are
not exactly one 120-byte record, so an unknown shape can never pass secret
bytes through). The export holds no communication password, PIN, card
identifier or biometric value by construction, and a test pins the exact
exported user keys plus the absence of the fixture credential marker.
Every export is audited as `diagnostics.export` with counts only — never
with bytes.

Diagnostics builds devices without write unlocks and offers no
write/delete/clear/set-time operation: investigation tooling must not
become a casual path to destructive actions.
