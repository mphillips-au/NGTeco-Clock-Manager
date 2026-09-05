# PHASE 16 — Synology / Linux Headless Service

## Goal
Reuse the proven device/sync core without the Windows GUI.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- PROTOCOL.md
- SECURITY.md
- TESTING.md

Build a Linux-compatible headless service and Docker packaging suitable for Synology Container Manager.

Reuse:
- MB1 protocol
- 120-byte parser
- attendance engine
- live capture
- reconciliation
- persistence

Do not duplicate protocol code.
Add health checks, structured logs, graceful shutdown and reconnect.
