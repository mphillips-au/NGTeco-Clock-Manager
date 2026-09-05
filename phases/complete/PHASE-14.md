# PHASE 14 — Production QA

## Goal
Harden the Windows application without adding major features.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- PROTOCOL.md
- SECURITY.md
- TESTING.md

Test startup, migrations, connection/reconnect, live capture, reconciliation, user CRUD, attendance, timesheets, reports, exports, roles, audit, backup/restore, offline mode, discovery and installer.

Focus on:
- 120-byte MB1 records
- no generic 72-byte user writes
- credential redaction
- attendance idempotence
- safe destructive actions
- privilege mapping
- live recovery
- timezone/DST
- malformed packets
