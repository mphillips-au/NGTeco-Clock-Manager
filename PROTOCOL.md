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

## Biometric / card investigation (PHASE 11)

Investigation only. No new device operation was implemented: nothing in this
section is proven on the project MB1, so every capability below keeps the
state it had before this phase. What follows is the evidence map for the
controlled device testing that must happen before anything is built.

Method used in this phase: pyzk 0.9 source reading, the pyzk NGTeco issue
history (notably `fananimi/pyzk` issue #240, NG-MB2 on the same firmware
`Ver 8.0.4.5-7108-02` and platform `ZMM510_TFT` family), the NG-MB1 user
manual (4-in-1: face, fingerprint, RFID card, PIN; 200 users / 200 faces /
400 fingerprints), static offset analysis of the verified 120-byte record,
and the pre-existing adapter. No real device was available in this session,
so no packet capture was taken and no disposable test user was exercised.
Each row records command, payload, response, structure, confidence,
reversibility and test status.

### Presence (counts, no template content)

`CMD_GET_FREE_SIZES` (pyzk `read_sizes()`) reports `users`, `fingers`,
`records`, `cards` and `faces` counters. The adapter already surfaces the
fingerprint and face counters as `DeviceInfo.fingerprint_count` /
`DeviceInfo.face_count`, and issue #240 marks `read_sizes` compatible on the
sibling NG-MB2. Confidence: MEDIUM for "the transport returns counters".
What the counters mean on an MB1 (enrolled templates vs capacity vs
availability) is UNVERIFIED, and no count has been cross-checked against a
known enrolled/unenrolled user. Reversibility: read-only, fully safe. Test
status: counters parsed from fakes/mocks only; needs a real-device read with
a known biometric state plus `get_face_version` / `get_face_fun_on` /
`get_fp_version` option reads as corroboration.

### Fingerprint bulk read (candidate, NOT proven)

pyzk `get_templates()`: `CMD_DB_RRQ` (7) with `FCT_FINGERTMP` (2), buffered
read with a 4-byte total-size prefix; each entry framed as
`unpack('HHbb', ...)` = size, UID, finger ID, valid flag, followed by
`size - 6` template bytes. Issue #240 marks it compatible on the NG-MB2.
Confidence: LOW for the MB1 — sibling-model report only, template byte
format (finger algorithm 10) unknown, and total-size accounting unconfirmed
against 120-byte-record devices. Reversibility: read-only, safe. Test
status: NOT RUN on the project MB1. Required before use: capture a bulk
read on a device with a known enrolled finger, verify entry framing and UID
mapping against the 120-byte user list, and confirm templates are never
logged or persisted (SECURITY.md forbids it).

### Fingerprint single read (evidence: INCOMPATIBLE, do not use)

pyzk `get_user_template()`: command 88 with `pack('hb', uid, temp_id)`,
up to 3 retries. Issue #240 marks it incompatible on NGTeco ("more testing
required"). Confidence: MEDIUM that it does not work as-is. Reversibility:
read-only. Test status: NOT RUN here; do not retry without a capture plan.

### Fingerprint enrollment (evidence: UNSAFE, do not implement)

pyzk `enroll_user()`: `CMD_STARTENROLL` (61), then a 60-second
socket-blocking multi-round capture loop. Issue #240 reports the device
shows the enrollment screen and then freezes. Confidence: MEDIUM that the
generic flow is wrong for this family. Reversibility: NOT REVERSIBLE
without a delete path, and a freeze risks forcing a power cycle mid-write.
Test status: NOT RUN here and must not be run until reads are proven. No
enrollment capability exists and none may be added on this evidence.

### Fingerprint upload / delete (NOT proven, do not implement)

pyzk `save_user_template()` packs the user with the generic 72-byte
`repack73()` layout, which is already proven incompatible with the 120-byte
MB1 record (see "Write protocol"), so the whole packet is suspect;
`delete_user_template()` (command 19, or 134 with a 24-byte user ID on TCP)
is untested on any NGTeco device in the available reports. Confidence: LOW.
Reversibility: upload overwrites device state; delete destroys templates.
Test status: NOT RUN. Both stay absent from the adapter and the interface.

### Face templates (NO KNOWN COMMAND, do not implement)

pyzk 0.9 has no face-template read/write API at all — only presence flags
(`get_face_version`, `get_face_fun_on` option reads, both marked compatible
on the NG-MB2). No command, payload or structure is known for face
enrollment, download, upload or delete on this protocol. Confidence: HIGH
that there is nothing to implement from. Reversibility: n/a. Test status:
NOT RUN. Face work needs fresh captures (option reads first, then
controlled enrollment observation), never code first.

### Card (ONE UNVERIFIED CANDIDATE OFFSET, do not implement)

One public snippet (issue #240, January 2026, brief MB1 access, self
described as lightly tested) claims the card is a 4-byte little-endian
value at record bytes 83:87. Against the verified layout that range falls
inside the last-name region (59:96), the same snippet reads last name as
59:99 (overlapping the verified user-ID field at 96:120) and reads the card
from the whole-buffer offset instead of the per-record offset, so the claim
contradicts verified field boundaries and contains its own packing bugs.
Confidence: VERY LOW — hypothesis only. The generic pyzk 72-byte record
does carry a 4-byte card, which proves only that ZKTeco devices *can* store
one, not where the MB1 stores it. Reversibility: a wrong-offset write would
corrupt names or credentials. Test status: NOT RUN. `WRITE_USER_CARD`
stays UNSUPPORTED. The proving test, when hardware is available: enroll a
card on a disposable user via the device keypad, dump the 120-byte record
before/after, and diff — implement nothing until the offset survives that.

## Relevant external research

The pyzk NGTeco MB1 work/issue is an important reference.
Use current repository references and documented findings as supporting evidence, but verify behavior against the actual MB1 before treating it as production truth.
