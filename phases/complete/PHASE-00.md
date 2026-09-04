# PHASE 00 — Repository Bootstrap

## Goal
Create the software foundation without device writes.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- TESTING.md

## Build
- Python 3.12+
- PySide6 shell
- SQLAlchemy + SQLite
- pytest
- ruff
- mypy where practical
- package structure separating domain, persistence, protocol, sync, services, GUI and security
- pyproject.toml
- entry point
- configuration
- logging
- DB bootstrap
- basic tests

## Rules
No production device writes.
No hardcoded production IP.
No secrets in source.

## Done when
App launches, DB initializes, tests pass and docs are updated.
