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
- bytes 3:11 = PIN, NUL-padded ASCII (PROVEN, PHASE 15)
- bytes 11:35 = rest of the credential region, unknown, always preserved
- bytes 35:59 = first name (24 bytes)
- bytes 59:96 = last-name region; only **23 bytes** are writable (PHASE 15)
- byte 87 = device-owned flag, always 0x01 (PHASE 15)
- bytes 96:120 = user-ID region; only **9 bytes** are writable (`~PIN2Width`)

Writing beyond a writable budget reaches bytes the device owns. See
`PROTOCOL.md` and `phases/PHASE-15.md`.

The device does **not** store the 120 bytes it is given: it sets byte 87 itself
and does not zero-fill field tails, so read-back verification compares decoded
fields, never bytes.

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

### Test-target authorisation (project NG-MB1, recorded PHASE 15)

The operator has given a standing, **narrow** exception to "modify production
users as part of a test", for the project clock (serial NBF6260700048) only:

- **UID 1 (Dean Gianginis) is off-limits.** Read only. Never write to it, never
  delete it, never touch its credential region.
- **UID 2 (Erin Stilo) may be modified**, but she is a real employee. Snapshot
  the whole 120-byte record first, prefer reversible changes, verify by
  read-back, and restore the original state before finishing. Deleting her may
  destroy her biometric enrolment; do not delete her.
- **Anything destructive or credential-related belongs on a throwaway
  `ZZTEST-` account** you create and delete: PIN set/clear, delete-and-verify,
  privilege changes, malformed input.

PHASE 15 used this authorisation and did **not** need to write to UID 2: every
question was settled on disposable accounts. Both real records were confirmed
byte-identical to their pre-write snapshots afterwards. Prefer that outcome.

Note that both enrolled users are privilege 14 (Admin), so UID 1 is not the
only administrator on the device.

### Hard-won limits for device writes

Learned the expensive way in PHASE 15 (`phases/PHASE-15.md`, "The incident"):

- **Never exceed a device-reported field width.** A 13-character user ID
  against `~PIN2Width=9` produced a record that could not be deleted, and the
  device lost both enrolled fingerprints and stopped answering the protocol for
  forty minutes. User IDs are bounded to 9 bytes and last names to 23; the
  builder enforces both.
- **Do not test malformed input against real hardware.** Malformed-input
  refusal is an application concern and belongs in unit tests. There is no
  version of "what does the device do with a bad packet?" that is worth the
  answer on a clock somebody depends on.
- **A device that accepts TCP is not a healthy device.** Port 4370 stayed open
  throughout the outage.
- **There is no remote reset when the protocol service is down.**
  `ZK.restart()` needs a working session. Plan writes on the assumption that
  nobody can power-cycle the clock.

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
