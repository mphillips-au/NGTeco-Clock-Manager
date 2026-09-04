# PHASE 01 — MB1 Read-Only Protocol Core

## Goal
Create the reusable NG-MB1 device layer.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- PROTOCOL.md
- TESTING.md

## Build
- device interface
- NGTecoMB1Device
- connect/disconnect
- device info
- time
- 120-byte MB1 user parser
- attendance retrieval
- live attendance
- retries/reconnect
- structured exceptions
- capability model

## User record
0:2 UID
2 privilege
3:35 credential/PIN area
35:59 first name
59:96 last name
96:120 user ID

Privilege 0 = Employee; 14 = Admin.

## Attendance
punch 0 = IN; punch 1 = OUT.
Preserve status as raw metadata.

## Tests
Sanitized fixtures, parser tests, attendance tests, mock device tests, opt-in real-device integration.

No writes to the real clock.
