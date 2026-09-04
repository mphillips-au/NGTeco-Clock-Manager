# PHASE 03 — MB1 User Management

## Goal
Implement safe user CRUD using the MB1 120-byte format.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- PROTOCOL.md
- SECURITY.md
- TESTING.md

## Build
- list/get/add/update/delete
- GUI search/filter/form
- Employee/Admin
- first name
- last name
- user ID
- PIN/password
- audit

## Critical
Never use generic pyzk.set_user() for MB1 writes without proving payload compatibility.

Build exact 120-byte records.

## Write sequence
read -> validate -> build -> send -> verify response -> read back -> compare -> audit

## Delete
Show exact user, warn about attendance history, require confirmation, delete, verify, audit.

Do not implement face/fingerprint writing.
Card writing remains unsupported unless proven.

Use disposable test users for real-device writes.
