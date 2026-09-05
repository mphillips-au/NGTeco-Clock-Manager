# PHASE 17 — Web/API

## Goal
Build the future web/API boundary over the headless core.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- SECURITY.md
- TESTING.md

Build an API (FastAPI unless architecture requires otherwise) for:
- auth
- roles
- devices
- employees
- users
- attendance
- live state
- timesheets
- reports
- audit
- sync

The browser must NEVER talk directly to TCP 4370.
Do not duplicate protocol logic.
