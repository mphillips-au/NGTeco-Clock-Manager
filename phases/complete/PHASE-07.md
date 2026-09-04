# PHASE 07 — Authentication / Roles / Audit

## Goal
Protect the application and separate admin/office access.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- SECURITY.md
- TESTING.md

## Roles
ADMIN, OFFICE_STAFF, VIEWER.

Admin gets all controls.
Office Staff gets normal office workflows but no developer/device destructive settings.
Viewer is read-only.

Implement:
- local accounts
- secure password hashing
- login/logout
- role enforcement
- hidden restricted screens
- audit log

Never log sensitive credentials.
