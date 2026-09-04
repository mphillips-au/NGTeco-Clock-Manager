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
