# Architecture

## Windows-first structure

```text
PySide6 GUI
    |
Application Services
    |
Domain Models / Business Rules
    |
Persistence
    |
NGTeco Device Adapter
    |
MB1 Protocol
    |
NG-MB1 TCP 4370
```

## Future structure: hosted portal + site agent (decided 2026-09-10)

```text
HOSTED (own VPS)
  Web browser (PHASE 19)
      |  HTTPS, session + TOTP
  Portal API (grown from clockmanager.api)
      employees, timesheets, reports, accounts,
      audit (system of record), agent ingest, command queue
      |
  Postgres (tenant_id on every table)

      ^
      |  HTTPS, opened OUTBOUND by the agent only
      |  (punches, heartbeat, inventory, snapshots, command long-poll)
      |  no inbound port at any site

EACH SITE (the clock's LAN)
  Site agent (PHASE 18): Windows service | systemd | Docker
      per-device owner: ONE session per clock
      sync engine, live capture, outbox (SQLite), local audit
      command executor + write gates (enforced here, not on the server)
      secrets: DPAPI (Windows) / 0600 file (Linux)
      |
  MB1 protocol adapter (clockmanager.protocol, agent only)
      |  TCP 4370, LAN only
  NG-MB1 clock(s)
```

- The **agent** owns every connection to a clock. It uploads punches,
  device inventory and device-user snapshots (never PINs, cards, templates
  or the device password). It long-polls the server for commands and
  enforces the write gates itself.
- The **server** owns people, time and money: employees, timesheets,
  reports, portal accounts, the command queue and the audit record.
- The **domain, sync engine, keys, security and logging** packages are
  shared. Protocol and diagnostics are agent-only. Employees, timesheets,
  reports and auth are server-only. The full package map, protocol,
  enrolment, threat model and roadmap are in `phases/PHASE-18.md`.
- Rejected: a single on-site box running `--serve`, `--api-serve` and the
  web UI.

## Separation

The reusable core must not import PySide6.

The GUI must call application services, not low-level protocol functions directly.

The protocol adapter must convert device-specific structures into stable domain models.

## Device abstraction

Use an interface/protocol for attendance devices so multiple devices can be supported later, while implementing NG-MB1 only initially.

## Persistence

SQLite initially, with a clean repository/service boundary so PostgreSQL can be introduced for the hosted portal. The desktop app and the site agent's local buffer stay on SQLite.

## Multiple devices

Even with one device initially:
- store device identity
- store attendance source device
- map users to devices
- don't hardcode one clock into business logic
