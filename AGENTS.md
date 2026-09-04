# AGENTS.md — NGTeco Clock Manager Agent Rules

## Purpose

This file contains the rules that apply to every Claude Code/Codex session in this repository.

## Session workflow

Before changing code:
1. Read `PLAN.md`.
2. Read `STATUS.md`.
3. Read `CHANGELOG.md`.
4. Read the current `phases/PHASE-XX.md`.
5. Read only the additional documents needed for that phase.
6. Inspect the existing implementation before modifying it.

Do not read the entire repository or all documentation unnecessarily.

At the end of every session:
1. Run relevant tests.
2. Run configured lint/type checks.
3. Perform the required manual verification.
4. Update `STATUS.md`.
5. Update `CHANGELOG.md`.
6. Record known limitations and protocol discoveries.
7. Report exactly what was tested.
8. Stop. Do not start another phase unless explicitly instructed.

## Phase discipline

- Work on one phase at a time.
- Do not silently pull future-phase functionality into the current phase.
- Preserve existing working functionality.
- Prefer incremental changes over rewrites.
- Do not remove working protocol behavior without evidence.
- If a requirement conflicts with observed device behavior, document the conflict rather than guessing.

## Product architecture

The product is a Windows-first desktop application built with Python 3.12+ and PySide6.

The core device/protocol and synchronization layers MUST NOT depend on PySide6.

The architecture must remain suitable for a later Linux/Synology headless service and future web/API layer.

Keep these concerns separated:
- domain
- persistence
- device/protocol
- synchronization
- application services
- GUI
- diagnostics/logging
- authentication/authorization

Design for one device initially, but do not hardcode a single-device architecture.

## NGTeco NG-MB1 facts

The actual device has been tested successfully.

Known:
- Device: NGTeco NG-MB1
- TCP protocol port: 4370
- Platform observed: ZMM510_TFT
- Firmware observed: Ver 8.0.4.5-7108-02
- ZKTeco-compatible protocol works.
- `pyzk` 0.9 can connect.
- `pyzk.live_capture()` works.
- Standard `pyzk.get_users()` parsing is NOT correct for this device.
- Standard `pyzk.set_user()` must NOT be assumed safe for this device.

### 120-byte MB1 user record

Known layout:
- bytes 0:2 = UID, little-endian uint16
- byte 2 = privilege
- bytes 3:35 = credential/PIN region
- bytes 35:59 = first name
- bytes 59:96 = last name
- bytes 96:120 = user ID

Known privilege values from the real device:
- 0 = Employee
- 14 = Admin

The custom 120-byte parser has been verified against multiple users on the real MB1.

### Attendance

Attendance records contain:
- UID
- user ID
- timestamp
- status
- punch

Observed:
- `punch=0` = IN
- `punch=1` = OUT

Do not infer IN/OUT from `status`. Preserve `status` as raw metadata.

## Security and sensitive data

Never log, print, export, or commit:
- PINs/passwords
- full card identifiers
- biometric templates
- authentication secrets

Use sanitized protocol fixtures.

Sensitive fields must be masked in GUI.

Destructive device operations require:
1. validation
2. explicit confirmation
3. command execution
4. read-back verification
5. audit logging

Never:
- factory reset
- clear attendance automatically
- delete users automatically
- modify production users as part of a test
- send an unverified user packet to an MB1

Use disposable test users for write testing.

## Protocol discipline

Do not replace an MB1-specific implementation with a generic ZKTeco implementation just because a generic library already has a method.

Use `pyzk` as:
- a proven protocol transport/reference where useful
- a source of constants/command behavior
- a useful compatibility layer

But keep an application-owned MB1 protocol adapter and domain models.

Unknown protocol behavior must be marked unsupported until proven.

## Testing

Any new non-trivial behavior must have automated tests.

Prefer:
- unit tests for packet parsing/building
- fixture-based tests
- mock-device tests
- integration tests gated behind explicit real-device configuration

Real-device tests must:
- be safe
- be intentional
- use disposable accounts where writes are required
- read back and verify writes

Do not make the test suite require a real clock by default.

## Documentation

Update documentation when:
- protocol behavior is discovered
- architecture changes
- security assumptions change
- phase milestones change
- limitations are discovered

`STATUS.md` is current truth.
`CHANGELOG.md` is historical truth.
`PROTOCOL.md` is device/protocol truth.
`PLAN.md` is product direction.

## Coding quality

- Python 3.12+
- clear type hints
- small functions
- explicit exceptions
- structured logging
- no hidden global state
- no hardcoded production IPs
- GUI I/O must not block the UI thread
- avoid giant modules/classes
- keep external dependencies minimal
- preserve migration compatibility
