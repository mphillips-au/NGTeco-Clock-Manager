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
