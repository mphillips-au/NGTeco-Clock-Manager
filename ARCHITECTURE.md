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

## Future structure

```text
Web Browser
    |
HTTPS / REST API
    |
Headless Linux Service
    |
Same NGTeco Core
    |
NG-MB1
```

## Separation

The reusable core must not import PySide6.

The GUI must call application services, not low-level protocol functions directly.

The protocol adapter must convert device-specific structures into stable domain models.

## Device abstraction

Use an interface/protocol for attendance devices so multiple devices can be supported later, while implementing NG-MB1 only initially.

## Persistence

SQLite initially, with a clean repository/service boundary so PostgreSQL can be introduced for the future NAS/web deployment.

## Multiple devices

Even with one device initially:
- store device identity
- store attendance source device
- map users to devices
- don't hardcode one clock into business logic
