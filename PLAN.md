# NGTeco Clock Manager — Project Plan

## Product

A polished Windows-first attendance/time-clock management application for NGTeco NG-MB1 devices, designed to later become a Synology-hosted headless service and web application.

## Current milestone

Windows desktop application first.

## Technology direction

- Python 3.12+
- PySide6
- SQLite initially
- SQLAlchemy
- pytest
- ruff
- mypy where practical
- packaging/installer for Windows

Future:
- headless Linux/Synology service
- REST API
- web frontend
- multi-device support

## Core capabilities

- device connection/settings
- automatic LAN discovery
- device diagnostics
- user management
- PIN/card capability where proven
- historical attendance sync
- live attendance events
- reconciliation
- employees
- timesheets
- pay periods
- reports
- exports
- roles
- audit log
- backup/restore
- offline operation

Biometric face/fingerprint management is investigation-gated and must not be guessed.

## Architecture

The reusable core must support both:
- Windows GUI
- future Linux/Synology service

The browser must never communicate directly with the MB1.

## Product principles

- fast
- modern
- reliable
- safe device writes
- real-world business usability
- strong diagnostics
- future multi-device compatibility
