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

Office Staff must not see:
- protocol tools
- raw packet data
- credential data
- destructive device settings

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

## User credential data

No user PIN, card identifier or biometric template is persisted anywhere.

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

Actor identity is the operating-system account. Roles arrive in PHASE 07;
claiming more identity than the application has would be a fiction.

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

## Future web

The browser must never connect directly to TCP 4370.
The headless service is the network boundary.

## Logging

Use structured logs with redaction.
Protocol debugging can expose raw packets only in an explicit admin/developer mode and must redact known secret regions.
