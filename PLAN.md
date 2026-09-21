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

## Web deployment decision (2026-09-10)

The web version is a **centrally hosted portal** plus a small **site agent**
on each clock's network (Windows service, Linux systemd or Docker).

- The agent is the only thing that talks to a clock (one owner per clock).
  It makes outbound HTTPS connections only, so no port is opened into an
  office.
- The server holds employees, timesheets, reports, portal accounts and the
  audit record, on Postgres. It is hosted on the operator's own VPS.
- The server never touches TCP 4370. Device write gates are enforced at the
  agent, and the server cannot override them.
- PINs, full card numbers, biometric templates and the device communication
  password never leave the site.
- Built for one business, multi-tenant-ready (`tenant_id` from day one).
  Portal sign-in uses local accounts with TOTP.

**Rejected:** one on-site box running `--serve`, `--api-serve` and the web
UI. The design brief and roadmap are in `phases/PHASE-18.md`; the web
frontend follows it as PHASE 19.

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

The browser must never communicate directly with the MB1. Neither must
the hosted server: only the site agent does.

## Product principles

- fast
- modern
- reliable
- safe device writes
- real-world business usability
- strong diagnostics
- future multi-device compatibility
