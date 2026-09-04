# PHASE 08 — Device Management / Discovery

## Goal
Support multi-device architecture while shipping one device first.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- PROTOCOL.md
- TESTING.md

## Build
Device records with name, IP, port, credentials, enabled, timeout, reconnect, last seen, firmware/platform/serial, sync state.

Discovery:
- manual IP
- LAN scan for TCP 4370
- safe device identification

Never modify discovered devices automatically.
