# PHASE 02 — Windows GUI / Settings / Diagnostics

## Goal
Build the first usable PySide6 application around the read-only core.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- SECURITY.md
- TESTING.md

## Build
- main window
- navigation
- dashboard
- device settings
- diagnostics
- users list
- attendance list
- live events

## Settings
- name
- IP
- port
- communication password
- timeout
- auto reconnect
- sync interval
- enabled

## Diagnostics
- test connection
- device info
- users
- attendance
- time
- live capture state
- safe logs

All device/network I/O must stay off the GUI thread.
No device writes yet.
