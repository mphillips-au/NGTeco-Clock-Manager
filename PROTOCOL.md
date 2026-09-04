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

## Discovery (PHASE 08)

Finding clocks is read-only. Probing (`protocol/discovery.py`) opens a TCP
connection to port 4370 and immediately closes it: reachability only, no
command sent, no authentication attempted. Identification connects, reads
the device snapshot (`connect()` returns `DeviceInfo`) and always
disconnects, including on failure; it performs no write of any kind.

A scan refuses ranges larger than 1024 addresses rather than sweeping a
whole site, and deriving the local subnet is best-effort: when no LAN can
be determined, discovery falls back to a manually entered address.

A discovery never becomes a stored device on its own. `register_discovered`
is the only path, and it needs an operator-supplied name, refuses duplicate
names and already-stored addresses, and writes locally only — the device
itself is never changed by being discovered.

## Diagnostics (PHASE 10)

Admin-only protocol diagnostics capture what the wire carries without
changing it (`protocol/trace.py`, `services/diagnostics.py`):

- connection report: timed connect, device-clock read and a
  disconnect/reconnect cycle, per step
- protocol trace: transport TX/RX on real hardware (a recording wrapper
  around the pyzk transport logs command codes, byte counts and
  redacted previews; the mock has no socket, so its trace says so and
  times device operations instead), raw + parsed user records, raw +
  parsed attendance, a short live-capture window and the capability
  report, every step timed
- raw reads stay inside the protocol layer: whole 120-byte records are
  redacted (credential region zeroed, wrong-sized buffers refused) before
  anything leaves it, and attendance bytes — which hold no credentials —
  are previewed bounded (128 bytes max)
- diagnostics builds devices without write unlocks and offers no
  write/delete/clear/set-time operation anywhere in the path

The mock carries raw user records like the adapter; it carries a raw
attendance payload only when its script is given a fixture one, otherwise
diagnostics shows parsed attendance with an explanatory note rather than
inventing bytes.

## Write protocol (PHASE 03)

Generic pyzk `set_user()` is NOT approved for MB1, and is never called.

The evidence, read from the pyzk 0.9 source: for any device whose
`user_packet_size` is not 28, `set_user()` builds
`pack("HB8s24s4sx7sx24s", ...)` — a **72-byte** packet
(2 + 1 + 8 + 24 + 4 + 1 + 7 + 1 + 24). The MB1 record is **120 bytes** with a
different field layout. The two are not compatible.

The application therefore builds the record itself
(`clockmanager.protocol.builders.build_user_record`) and sends it with
`CMD_USER_WRQ` (8), followed by `CMD_REFRESHDATA` (1013) so the device reloads
its interior data before the read-back.

Deletion uses `CMD_DELETE_USER` (18) with a two-byte little-endian UID payload.
That command is model independent, but destructive testing remains controlled.

### Write sequence

Every write performs, inside the adapter so a caller cannot skip a step:

1. read the device's user records whole
2. validate the request against what is actually on the device
3. build the exact 120-byte record
4. send it
5. require an acknowledgement (`status` true from the command response)
6. read the records back
7. compare

Comparison is byte-exact outside the credential region. The credential region
is compared on presence only: a device may store a PIN in a transformed form,
so comparing those bytes would fail spuriously and would require handling the
secret.

Writes are never retried. A retry could apply the same change twice, and a
write whose outcome is unknown must be investigated rather than repeated.

### Status: UNVERIFIED

No NG-MB1 has yet accepted a record from this path. `Capability.WRITE_USERS`
and `Capability.DELETE_USERS` are `UNVERIFIED` and unusable until an operator
unlocks them, at which point they report `OPERATOR_ENABLED`, never `SUPPORTED`.
`tests/integration/test_real_device_writes.py` is the suite that would change
that.

## Credentials

The user record contains a credential/PIN region at bytes 3:35 (32 bytes). Do
not expose its contents in normal tooling.

**Its internal layout is unknown.** The application treats it as opaque:

- an update copies the existing 32 bytes byte-for-byte, so changing a name
  cannot destroy a user's PIN
- clearing zeroes the whole region
- setting a PIN writes into bytes 3:11 only, a candidate offset inferred from
  the generic ZKTeco record whose 8-byte password field follows the privilege
  byte. Everything outside that field is preserved, so a wrong guess damages as
  little as possible.

Setting or clearing a credential is gated behind
`Capability.WRITE_USER_PASSWORD`, which is UNVERIFIED and requires its own
operator unlock.

## Cards

No card field has been identified in the 120-byte record.
`Capability.WRITE_USER_CARD` is UNSUPPORTED and cannot be unlocked.

## Biometrics

Face/fingerprint support has not been fully reverse engineered.
Do not implement guesses.
Investigate using controlled, disposable users and protocol captures.

## Relevant external research

The pyzk NGTeco MB1 work/issue is an important reference.
Use current repository references and documented findings as supporting evidence, but verify behavior against the actual MB1 before treating it as production truth.
