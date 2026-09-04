# NGTeco NG-MB1 Protocol Notes

## Verified

Actual MB1 tested successfully on TCP 4370.

Observed device:
- NG-MB1
- ZMM510_TFT
- firmware Ver 8.0.4.5-7108-02

`pyzk` can connect.
`live_capture()` works.

## User record

The MB1 user data response contains:
- 4-byte total-size prefix
- 120 bytes per user

Record layout currently verified:
- 0:2 UID uint16 LE
- 2 privilege byte
- 3:35 credential/PIN region
- 35:59 first name
- 59:96 last name
- 96:120 user ID

Known privilege:
- 0 Employee
- 14 Admin

## Attendance

Observed fields:
- UID
- user ID
- timestamp
- status
- punch

Observed:
- punch 0 = IN
- punch 1 = OUT

Status is independent raw metadata.

## pyzk integration boundary (PHASE 01)

`pyzk` 0.9 is used as the transport, command encoder and connection handler.
Everything the MB1 does differently is owned by this application.

Established by reading the pyzk 0.9 source against the MB1 record layout:

- `ZK.get_users()` only handles 28- and 72-byte records. For a 120-byte MB1
  record it logs a warning and then parses the buffer in 72-byte strides,
  producing garbage users rather than failing. It must never be used.
- `ZK.get_attendance()` calls `self.get_users()` internally to map UIDs to user
  IDs for the 8- and 16-byte record forms, so it inherits that mis-parse.
  Attendance is therefore parsed by this application instead.
- `ZK.live_capture()` also calls `self.get_users()`, and for any user it cannot
  find in that list it evaluates `uid = int(user_id)`. On an MB1 with a
  non-numeric user ID such as `EMP-003` this raises `ValueError` and kills the
  capture loop. The live loop is therefore driven directly from the socket.

The live loop depends on pyzk's name-mangled `_ZK__sock` and `_ZK__ack_ok`.
That dependency is isolated in one accessor that raises a clear
`DeviceProtocolError` if a future pyzk removes them, and `pyzk` is pinned to
`==0.9` for this reason.

## Attendance record sizes

The ZKTeco protocol defines 8-, 16- and 40-byte attendance records. Which one
an MB1 uses has NOT been confirmed on the real device; the size is resolved at
runtime from the payload and the device's own record count.

The record count is authoritative. Length alone is ambiguous, because every
multiple of 40 is also a multiple of 8 and 16 — a 2-record 40-byte payload
would otherwise parse as 10 fabricated 8-byte records. An ambiguous payload is
refused rather than guessed at.

## Write protocol

Generic pyzk `set_user()` is NOT approved for MB1.
The generic implementation uses the wrong user packet shape.

The MB1 write path must use an explicitly verified 120-byte record.

Deletion appears to use the generic delete-user command with UID payload, but real-device destructive testing must remain controlled.

## Credentials

The user record contains a credential/PIN region. Do not expose its contents in normal tooling.

## Biometrics

Face/fingerprint support has not been fully reverse engineered.
Do not implement guesses.
Investigate using controlled, disposable users and protocol captures.

## Relevant external research

The pyzk NGTeco MB1 work/issue is an important reference.
Use current repository references and documented findings as supporting evidence, but verify behavior against the actual MB1 before treating it as production truth.
