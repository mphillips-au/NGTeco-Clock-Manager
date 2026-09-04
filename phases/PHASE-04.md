# PHASE 04 — Attendance Synchronization

## Goal
Build reliable live + historical attendance synchronization.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- PROTOCOL.md
- TESTING.md

## Build
- initial full sync
- incremental reconciliation
- manual sync
- live capture
- background sync
- reconnect
- duplicate prevention
- sync history
- offline recovery

Store:
- device
- UID
- user ID
- employee
- timestamp
- punch
- status
- received_at
- source
- event key

Use punch for IN/OUT.

Test duplicates, reconnects, missed live events, unknown UID and repeated sync.
