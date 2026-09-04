# ClockBridge

### Modern, open-source time & attendance management for biometric time clocks.

ClockBridge is a modern desktop time and attendance application designed to connect directly to biometric time clocks, manage employees and device users, synchronize attendance records, calculate working hours, and produce reports — without relying on a vendor cloud service.

> **Built for Windows today. Designed for Linux, NAS, and web deployments tomorrow.**

![ClockBridge](docs/images/clockbridge-dashboard.png)

---

## ✨ Why ClockBridge?

Traditional biometric time-clock software can be difficult to work with:

* Vendor-specific desktop applications
* Cloud accounts and subscriptions
* Limited reporting
* Closed ecosystems
* Poor APIs
* Difficult backups
* No meaningful offline mode
* Little control over your own attendance data

ClockBridge takes a different approach.

Your time clock is treated as a **device**, not a cloud service.

Attendance data belongs in your database.
Employees belong in your application.
Reports belong to you.

And the application communicates with the clock directly over the local network.

---

## 🚀 What it does

ClockBridge is being built around a simple architecture:

```text
                 ┌──────────────────────┐
                 │      ClockBridge     │
                 │                      │
                 │  Desktop Application │
                 └──────────┬───────────┘
                            │
                    Local Network
                            │
                            ▼
                 ┌──────────────────────┐
                 │    Time Clock        │
                 │                      │
                 │  NGTeco NG-MB1       │
                 │  TCP :4370           │
                 └──────────────────────┘
                            │
                            ▼
                    Attendance Events
                            │
                            ▼
                 ┌──────────────────────┐
                 │       SQLite         │
                 │                      │
                 │ Employees             │
                 │ Device Users          │
                 │ Attendance            │
                 │ Timesheets            │
                 │ Audit Logs            │
                 └──────────────────────┘
```

The long-term architecture also supports a headless service:

```text
             Windows Desktop
                    │
                    │
                REST API
                    │
                    ▼
             ClockBridge Service
                    │
             ┌──────┴──────┐
             │             │
          Database      Time Clock
             │
             ▼
          Reports
```

This makes it possible to eventually run ClockBridge on a Linux server, Synology NAS, or Docker host while accessing it from a browser.

---

# 🕒 Features

## Device management

* Connect directly to supported time clocks
* Device information
* Firmware information
* Serial number
* MAC address
* Platform information
* Device clock/time
* Connection diagnostics
* Network configuration
* Device discovery
* Online/offline status
* Connection/reconnection handling
* Multi-device-ready architecture

---

## 👥 Employee management

Manage employees independently from the physical clock.

Planned functionality includes:

* Employee profiles
* Employee ID
* First and last name
* Active/inactive status
* Department
* Position
* Notes
* Device assignments
* Attendance history
* Timesheets

ClockBridge deliberately separates an **employee** from a **device user**.

This allows the same employee to eventually exist across multiple clocks without coupling your business data to a particular device.

---

## 🔐 Device users

For supported devices, ClockBridge can work with device-level user records.

The NGTeco NG-MB1 uses a proprietary variation of the ZKTeco-style protocol and stores users using a 120-byte record format.

ClockBridge implements an NGTeco-specific parser rather than relying blindly on the generic ZKTeco user format.

Supported/targeted device-user fields include:

* User ID
* First name
* Last name
* Privilege
* PIN/password
* Card credentials
* Device-specific metadata

### Administrator & employee privileges

Device privileges are represented independently from ClockBridge application roles.

For example:

| ClockBridge       | Device           |
| ----------------- | ---------------- |
| Application Admin | Device Admin     |
| Office Staff      | Device Employee  |
| Viewer            | No device access |

This separation is intentional.

---

# 🕐 Attendance

ClockBridge supports both **historical synchronization** and **live attendance events**.

### Historical synchronization

The application can retrieve attendance records from the device and reconcile them with its local database.

This allows ClockBridge to recover from:

* Application shutdowns
* Network outages
* Device disconnections
* Missed live events
* Temporary server downtime

### Live attendance

Where supported, ClockBridge can listen for attendance events directly from the device.

A typical event looks like:

```text
Employee:      1
User ID:       1
Timestamp:     2026-08-28 15:21:52
Punch:         IN
Raw Status:    1
```

Live events are useful for dashboards and immediate feedback, but **live capture is never treated as the sole source of truth**.

Historical reconciliation remains part of the synchronization design.

---

# 📊 Timesheets

ClockBridge is designed to turn raw punches into useful working-time information.

Planned functionality includes:

* Daily hours
* Weekly hours
* Pay-period hours
* Clock-in/out pairs
* Missing punches
* Duplicate punches
* Overnight shifts
* Long shifts
* Maximum-hours warnings
* Attendance exceptions
* Manual corrections
* Employee timesheets

---

# 📅 Pay periods

ClockBridge supports configurable pay periods:

* Weekly
* Bi-weekly
* Semi-monthly
* Monthly

Additional configuration includes:

* Start day of week
* Day cutoff time
* Duplicate punch interval
* Maximum work hours
* Decimal hours
* HH:MM display

The goal is to keep payroll calculations predictable while preserving the original raw attendance records.

---

# 📈 Reports & exports

Planned reporting includes:

* Daily attendance
* Employee timesheets
* Weekly timesheets
* Pay-period reports
* Attendance exceptions
* Device activity
* Synchronization history
* Audit logs

Export formats:

* CSV
* XLSX
* PDF
* JSON

The underlying attendance data remains accessible rather than being trapped inside a proprietary report format.

---

# 🔄 Synchronization

ClockBridge is designed around a **local source-of-truth database**.

The synchronization engine is responsible for:

```text
             Time Clock
                  │
                  ▼
          Fetch attendance
                  │
                  ▼
            Normalize
                  │
                  ▼
              Deduplicate
                  │
                  ▼
            Store locally
                  │
                  ▼
           Build timesheets
```

Synchronization will be safe to run repeatedly.

If the same attendance record is encountered again, ClockBridge should recognize it rather than creating duplicate records.

---

# 🛡️ Safety first

Biometric devices are not forgiving development targets.

ClockBridge therefore follows several important rules:

### Read before write

Before modifying an existing device record:

1. Read the current record
2. Preserve fields that are not being changed
3. Construct the device-specific record
4. Write the record
5. Read it back
6. Verify the result
7. Record the operation in the audit log

### No generic user writer

The NGTeco NG-MB1 does **not** use the standard 28/72-byte ZKTeco user record format for its user database.

ClockBridge therefore does not simply call the generic `pyzk.set_user()` implementation and hope for the best.

The NGTeco-specific 120-byte format is handled by the device adapter.

### Unsupported ≠ implemented

If a device feature has not been properly understood, ClockBridge will not pretend to support it.

Fingerprint and face operations are therefore feature-gated until their protocol and data formats are understood and tested safely.

---

# 🔬 NGTeco NG-MB1

The first supported device is the:

**NGTeco NG-MB1**

The device has been tested on real hardware with:

```text
Firmware:  Ver 8.0.4.5-7108-02
Platform:  ZMM510_TFT
TCP Port:  4370
SSH Port:  3718
```

Verified functionality includes:

* Device connection
* Device information
* Device time
* Serial number
* MAC address
* Platform information
* Attendance retrieval
* Live attendance capture
* User retrieval
* NGTeco 120-byte user parsing
* User deletion
* Device diagnostics

Additional device functionality is being investigated.

---

# 🧩 Device adapter architecture

ClockBridge is not intended to become an application permanently hard-coded around one clock.

The core application communicates through a device abstraction:

```text
                    ClockBridge Core
                           │
                    Device Interface
                           │
             ┌─────────────┴─────────────┐
             │                           │
      NGTeco MB1 Adapter          Future Adapter
             │                           │
             ▼                           ▼
       NGTeco Protocol             Other Device
```

This makes it possible to eventually support additional hardware without rewriting the entire application.

---

# 🖥️ Desktop application

The Windows application is being built with:

* Python
* PySide6
* Qt 6
* SQLAlchemy
* SQLite
* Alembic
* pytest

The UI is designed to feel like a modern Windows application rather than a traditional Python utility.

Design goals include:

* Dark and light themes
* Responsive layouts
* Sidebar navigation
* Dashboard cards
* Modern tables
* Search and filtering
* Toast notifications
* Clear connection states
* Background device operations
* High-DPI support
* Keyboard-friendly workflows

---

# 🏗️ Architecture

ClockBridge follows a layered architecture:

```text
┌─────────────────────────────────────────┐
│                  GUI                    │
│               PySide6 / Qt              │
├─────────────────────────────────────────┤
│            Application Services         │
│     Users • Attendance • Sync • Reports │
├─────────────────────────────────────────┤
│               Domain Layer              │
│ Employees • Devices • Punches • Shifts  │
├─────────────────────────────────────────┤
│             Device Abstraction          │
├─────────────────────────────────────────┤
│          NGTeco MB1 Protocol            │
├─────────────────────────────────────────┤
│               Transport                 │
│                 TCP                     │
├─────────────────────────────────────────┤
│              Persistence                │
│           SQLAlchemy / SQLite           │
└─────────────────────────────────────────┘
```

The GUI should never need to know how a particular device packet is constructed.

Likewise, the device adapter should not know anything about Qt widgets.

This separation is important for the eventual Linux service and API.

---

# 🐧 Future: Linux & NAS

One of the major goals of ClockBridge is to make the core application portable.

The eventual architecture is:

```text
                    Browser
                       │
                       ▼
                ┌─────────────┐
                │  Web / API  │
                └──────┬──────┘
                       │
                       ▼
              ┌─────────────────┐
              │ ClockBridge      │
              │ Service          │
              └────────┬────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
         PostgreSQL          Time Clock
```

This could run on:

* Linux
* Docker
* Synology NAS
* Mini PCs
* Home servers
* Office servers
* Cloud/VPS infrastructure

The Windows desktop application can then become one client of the same underlying service.

---

# 🌐 Future API & Web UI

A future API is planned using FastAPI.

The API will eventually expose functionality such as:

```text
GET    /devices
GET    /employees
GET    /attendance
GET    /timesheets

POST   /sync
POST   /employees
POST   /device-users

GET    /reports
GET    /audit
GET    /health
```

The web application will **never communicate directly with TCP port 4370**.

Instead:

```text
Browser
   │
   ▼
HTTPS
   │
   ▼
ClockBridge API
   │
   ▼
ClockBridge Device Service
   │
   ▼
Time Clock
```

This keeps device communication isolated from the browser and makes authentication, authorization, auditing, and network security manageable.

---

# 🔐 Security

ClockBridge is intended to handle sensitive attendance and credential information responsibly.

Security principles include:

* Password hashing
* Role-based access control
* Audit logging
* No PINs in normal application logs
* No biometric templates in normal logs
* Sanitized protocol diagnostics
* Local database protection
* Explicit destructive-operation confirmation
* Least-privilege application roles
* Secrets kept out of source control

Diagnostic logging must never become a backdoor for accidentally exposing employee credentials.

---

# 👤 Application roles

ClockBridge is designed around application-level roles.

### Administrator

Full system access.

Can:

* Configure devices
* Manage users
* Manage employees
* Synchronize devices
* Configure pay periods
* Manage application users
* View audit logs
* Run diagnostics

### Office Staff

Day-to-day operational access.

Can:

* Manage employees
* View attendance
* Manage timesheets
* Run reports
* Perform normal synchronization

Device/network administration can remain restricted.

### Viewer

Read-only access.

Can:

* View dashboards
* View employees
* View attendance
* View timesheets
* View reports

Cannot modify device or employee data.

---

# 🧪 Development status

> **ClockBridge is currently under active development.**

The project is being developed in phases, with real hardware testing performed against an NGTeco NG-MB1.

The project is intentionally conservative around device writes and biometric functionality.

### Current focus

* [x] Device communication research
* [x] NGTeco MB1 protocol investigation
* [x] Device information
* [x] Attendance retrieval
* [x] Live attendance capture
* [x] NGTeco-specific user parsing
* [x] Project architecture
* [ ] Production desktop application
* [ ] User management UI
* [ ] Safe user writes
* [ ] Card support
* [ ] Attendance synchronization engine
* [ ] Employee management
* [ ] Timesheets
* [ ] Reports
* [ ] Authentication/RBAC
* [ ] Backup/restore
* [ ] Windows packaging
* [ ] Linux service
* [ ] REST API
* [ ] Web application
* [ ] Additional device support
* [ ] Fingerprint protocol support
* [ ] Face protocol support

Features marked as planned should not be considered production-ready until they are implemented and tested.

---

# 📦 Installation

> Installation instructions will be finalized as the first release is packaged.

For development:

```bash
git clone https://github.com/YOUR_USERNAME/clockbridge.git
cd clockbridge
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the application:

```bash
python -m clockbridge
```

Run tests:

```bash
pytest
```

---

# 🛠️ Development

ClockBridge is intentionally structured so that developers can work on individual layers without needing physical hardware for every change.

The project will provide:

* Unit tests
* Protocol fixtures
* Simulated device responses
* Database tests
* Synchronization tests
* Parser tests
* Integration tests
* Hardware tests where appropriate

Hardware-dependent tests should be explicitly enabled rather than silently modifying a real device.

---

# 🤝 Contributing

Contributions are welcome.

There are several ways to help:

### 💻 Code

* Device adapters
* Protocol research
* UI components
* Database improvements
* Synchronization logic
* Reporting
* API development
* Testing

### 🔬 Protocol research

This is particularly valuable.

If you have an unsupported NGTeco/ZKTeco-family device, useful information includes:

* Exact model
* Firmware version
* Platform
* Network protocol behavior
* Packet captures
* Device responses
* User record layouts
* Attendance formats
* Card behavior
* Fingerprint behavior
* Face behavior

Please remove passwords, credentials, employee information, biometric data, and other sensitive information before publishing captures.

### 🎨 UI/UX

Design improvements are welcome.

The goal is a professional application that is pleasant for office staff to use every day.

### 🐛 Bug reports

When reporting a device-related issue, please include:

```text
Device model:
Firmware:
Platform:
OS:
ClockBridge version:
Python version:
What happened:
Expected behavior:
Relevant logs:
```

Never include passwords, PINs, biometric templates, or other sensitive data in an issue.

---

# ⚠️ Important disclaimer

ClockBridge is an independent open-source project.

It is **not affiliated with, endorsed by, or supported by NGTeco, ZKTeco, or any other device manufacturer** unless explicitly stated.

Device protocols may be undocumented, proprietary, incomplete, or different between firmware versions and models.

Use write functionality carefully and test against non-production devices/users whenever possible.

**Always maintain an independent backup of important attendance data.**

---

# 📜 License

ClockBridge is released under the **MIT License**.

See [`LICENSE`](LICENSE) for details.

---

# ⭐ Why open source?

Time and attendance data is important.

Organizations should be able to understand:

* where their data is stored,
* how their attendance calculations work,
* how their time clocks communicate,
* how reports are generated,
* and how their software behaves when the internet disappears.

ClockBridge aims to provide that transparency.

No mandatory cloud.

No vendor lock-in.

No black-box attendance database.

Just a local-first, extensible time and attendance platform.

---

# 🗺️ Roadmap

### Phase 1 — Device foundation

* Device communication
* Protocol adapter
* Device information
* Attendance
* User parsing

### Phase 2 — Desktop application

* Modern Windows UI
* Device management
* Diagnostics
* User management

### Phase 3 — Attendance platform

* Synchronization
* Employees
* Timesheets
* Pay periods
* Reports

### Phase 4 — Production

* Authentication
* RBAC
* Audit logs
* Backup/restore
* Installer
* Reliability testing

### Phase 5 — Hardware expansion

* Card support
* Fingerprint investigation
* Face investigation
* Additional device adapters

### Phase 6 — Server edition

* Linux service
* Docker
* Synology support
* API
* PostgreSQL support

### Phase 7 — Web application

* Browser dashboard
* Live attendance
* Employee management
* Timesheets
* Reports
* Device administration

---

# 💡 Project philosophy

ClockBridge follows a few simple principles:

> **Local first.**

Your attendance data should remain useful without an internet connection.

> **Device-agnostic core.**

The business logic should not care which time clock produced a punch.

> **Safe writes.**

Never modify a device record blindly.

> **Raw data matters.**

Keep original device information alongside normalized application data.

> **Offline resilience.**

A temporary network failure should not destroy attendance history.

> **Transparent software.**

Users should be able to inspect, export, back up, and understand their own data.

> **Don't fake support.**

If a hardware feature isn't understood, it stays disabled until it can be implemented safely.

---

## 🌟 If this project is useful to you

Give the repository a ⭐ on GitHub.

If you have an NGTeco or compatible biometric time clock that you'd like to see supported, open an issue with the device model and firmware information.

And if you've reverse-engineered one of these devices before, **please get in touch** — protocol knowledge is one of the most valuable contributions this project can receive.

---

<div align="center">

**ClockBridge**

*Open-source time & attendance management.*

Built with ❤️ and a suspicious amount of packet analysis.

</div>
