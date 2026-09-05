# Testing Strategy

## Unit

Test:
- MB1 120-byte parser
- 120-byte packet builder
- privilege mapping
- attendance conversion
- duplicate detection
- time calculations
- pay-period calculations
- report calculations
- authorization
- diagnostics redaction (credential region zeroed, previews bounded)
- diagnostics timing and failure-as-result behavior

## Fixtures

Use sanitized MB1 fixtures representing:
- Admin
- Employee
- first/last names
- user IDs
- different PIN lengths without storing real credentials
- attendance records

## Mock device

Provide a simulated device for:
- connect
- device info
- users
- attendance
- live events
- writes
- deletes
- failures
- diagnostics (raw user records; raw attendance only from a fixture payload)

## Integration

Real device integration tests must be opt-in.

Use:
- explicit environment/config switch
- disposable test users
- read-back verification

Never require the production clock for normal CI.

Write tests need a **second** switch beyond the device address
(`CLOCKMANAGER_TEST_ALLOW_WRITES=1`), and credential writes a third
(`CLOCKMANAGER_TEST_ALLOW_CREDENTIAL_WRITES=1`). Every account they touch
carries the `ZZTEST-` prefix and is removed afterwards; nothing without that
prefix is ever modified or deleted.

## GUI

Use smoke tests and service-layer tests.
Keep critical business logic outside widgets.

## QA

Before release test:
- install
- upgrade
- migration
- offline
- reconnect
- live capture
- reconciliation
- user writes
- reports
- permissions
- backup/restore

### Running the read-only suite against the real clock

```
CLOCKMANAGER_TEST_DEVICE_HOST=<ip>   .venv/Scripts/python.exe -m pytest tests/integration/test_real_device.py -m real_device
```

Find the address with the application's own discovery rather than guessing:
`scan_hosts(hosts_from_cidr("192.168.0.0/24"))` probes TCP 4370 and sends no
command. Nothing in that suite writes.

### PHASE 14 status

Everything in the QA list above has been exercised except **user writes**,
which stay off by default and unproven. Install and upgrade were covered once
the Windows packaging work landed: the packaged executable migrated a schema-7
database forward with no data loss, and a silent install/uninstall cycle left
the user data directory untouched. See `STATUS.md` ("Verified on hardware")
for what each one showed.
