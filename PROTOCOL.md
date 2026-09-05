# NGTeco NG-MB1 Protocol Notes

## Verified

Actual MB1 tested successfully on TCP 4370.

Observed device:
- NG-MB1
- ZMM510_TFT
- firmware Ver 8.0.4.5-7108-02
- serial NBF6260700048

`pyzk` can connect.
`live_capture()` works.

PHASE 14 ran this application (not raw pyzk) against that device end to end:
LAN discovery, connect/reconnect, device info, clock read, the 120-byte user
parse, attendance retrieval, three-times-idempotent sync, a real badged live
event, live recovery, protocol tracing with credential redaction, and offline
failure handling. The write path was deliberately not exercised and remains
UNVERIFIED.

## User record

The MB1 user data response contains:
- 4-byte total-size prefix
- 120 bytes per user

Record layout currently verified:

```text
  0:2    UID, little-endian uint16
  2      privilege byte
  3:11   PIN, NUL-padded ASCII                     (PHASE 15)
 11:35   remainder of the credential region, unknown, always preserved
 35:59   first name (24 bytes)
 59:83   last name  (24 bytes usable)              (PHASE 15)
 83:87   unknown, zero on every record observed
 87      device-owned flag, always 0x01            (PHASE 15)
 88:90   unknown, zero
 90      0x01 on biometrically-enrolled users only, UNVERIFIED
 91:96   unknown, zero
 96:120  user ID (24-byte field, 9 bytes usable)   (PHASE 15)
```

Parsing reads 35:59, 59:96 and 96:120 and splits each on its NUL terminator,
which stays correct. What PHASE 15 changed is the **writable** budget: see
"Field budgets" below.

Known privilege:
- 0 Employee
- 14 Admin

## Attendance

Observed fields:
- record index (40-byte form; see "40-byte record layout" below)
- user ID
- timestamp
- status
- punch

Observed:
- punch 0 = IN
- punch 1 = OUT

Both confirmed on the real device in PHASE 14, including a live badge that
arrived as punch 1 (OUT).

Status is independent raw metadata.

Six records read in PHASE 15 across two users carried `status` 1 (three times)
and 15 (three times). Those are the standard ZKTeco verification-method codes
for fingerprint and face, and this device had exactly one fingerprint and one
face enrolled per user, so "`status` is the verification method" is a strong
inference -- but it is **not proven**, and `status` stays opaque until someone
badges once by finger and once by face and the log is read. Direction is never
inferred from it.

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

The ZKTeco protocol defines 8-, 16- and 40-byte attendance records. The project
NG-MB1 uses **40**, confirmed on the real device in PHASE 14: a 3-record read
returned a 124-byte payload (4-byte prefix + 120-byte body) while the device
reported 3 records. The size is still resolved at runtime rather than assumed,
because other firmware in the family may differ.

The record count is authoritative. Length alone is ambiguous, because every
multiple of 40 is also a multiple of 8 and 16 — a 2-record 40-byte payload
would otherwise parse as 10 fabricated 8-byte records. An ambiguous payload is
refused rather than guessed at.

This is not a theoretical safeguard on an MB1: every third 40-byte record makes
the body an exact multiple of 8 *and* of 40, so the ordinary case is ambiguous
and the count is what resolves it.

### 40-byte record layout (verified, PHASE 14)

```text
0:2    record index, little-endian uint16  -- NOT the user's device UID
2:26   user ID text, NUL-padded (24 bytes)
26     status (raw metadata)
27:31  packed 4-byte timestamp
31     punch (0 IN, 1 OUT)
32:40  reserved, observed all zero
```

The leading uint16 is the **attendance record's own index**, not the user's
UID. Three consecutive punches by the single enrolled user (device UID 1)
carried 1, 2 and 3 there while every user-ID field read `"1"`:

```text
rec 0: 01 00 | "1" ... | 01 | 39 ec 1a 33 | 00 | 00...
rec 1: 02 00 | "1" ... | 01 | 8b ff 1a 33 | 01 | 00...
rec 2: 03 00 | "1" ... | 0f | b4 1f 1b 33 | 00 | 00...
```

The parser therefore reports `device_uid=None` for this form and refuses a
record whose user-ID text is empty rather than attributing the punch to the
index. Schema 8 clears the indices that earlier reads stored.

Observed `status` values include 1 and 15 on the same device; `status` remains
raw metadata and is never used to infer direction.

## Timestamp range

Neither packed encoding has an invalid representation: every 32-bit value
decodes to some calendar date, so a corrupt or truncated packet yields a punch
that looks legitimate (`0xffffffff` decodes to the year 2133). Both encodings
count from 2000, so decoding bounds the year to 2000-2099 and refuses anything
outside it rather than storing a punch that would be counted in a pay period.

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

### Status: PROVEN (PHASE 15)

The real NG-MB1 (serial NBF6260700048) accepted application-built 120-byte
records and applied them. Verified on hardware, on disposable `ZZTEST-`
accounts: create, rename, privilege 0 -> 14 -> 0, PIN set, rename with the PIN
preserved, PIN clear, and delete -- every step confirmed by the adapter's own
read-back.

`WRITE_USERS`, `DELETE_USERS` and `WRITE_USER_PASSWORD` are `SUPPORTED`.

**Support is not permission.** Every device is still built with writing locked:
an installation that has not set `CLOCKMANAGER_ENABLE_DEVICE_WRITES` reports
those capabilities as `OPERATOR_LOCKED` -- proven, not usable here -- and the
adapter refuses them. Proving a write works on hardware must not be the same act
as turning it on everywhere.

### Read-back verification compares meaning, not bytes

**The MB1 does not store the 120 bytes it is given.** Byte-exact comparison
outside the credential region fails on *every* write to this device, which is
why the write path appeared broken for three phases while actually working.

Two behaviours:

1. **The device owns byte 87.** It reads back `0x01` on every stored record
   whatever was sent.
2. **The device does not zero-fill field tails.** Bytes after a field's NUL keep
   whatever the slot previously held. A record written into a slot that had held
   "Dean" read back with `n` still at offset 38, and the live UID 2 record
   carries `nis` at 65:68 -- the tail of "Gianginis" from UID 1:
   `53 74 69 6c 6f 00 6e 69 73`.

Verification therefore compares the decoded fields -- UID, user ID, first name,
last name, privilege -- plus credential presence. Residue and device-owned flags
are ignored; a field the device actually stored differently still fails.

### Field budgets (PHASE 15)

The record's regions are larger than what the device keeps:

- **User ID: 9 bytes.** The device reports `~PIN2Width=9`. See "The UID 901
  incident".
- **Last name: 23 bytes.** A 30-character last name came back truncated to 23,
  with the 24th byte zeroed by the device and byte 88 overwritten.
- First name: 24 bytes, unchanged.

Both are enforced in `build_user_record` as well as `UserDraft.validate`, so a
caller that builds a record directly cannot skip the check.

### Application-chosen UID: accepted

A record built with UID 900 -- above the device's 200-user capacity -- was
stored, read back as UID 900 and deleted cleanly. The device does not insist on
assigning UIDs itself. The application still uses the lowest free UID by
default.

### The UID 901 incident

A user record written with a **13-character user ID**, against the device's
stated `~PIN2Width=9`, was accepted and read back intact. Afterwards:

- `CMD_DELETE_USER` on that UID was acknowledged and did nothing, repeatedly,
  across reconnects and a reboot. The record is still on the device.
- The fingerprint store went from 2 templates to 0. Faces, PINs, user records
  and attendance were all unaffected.
- A further write to that UID timed out and the device stopped completing ZK
  sessions for about forty minutes while still accepting TCP on 4370. It
  recovered on its own and was then rebooted with `CMD_RESTART`.

**Attribution is not established.** Two well-formed deletes happened between the
long-ID write and the first observation of `fingers=0`, so a delete cannot be
excluded as the mechanism. The long user ID is the only out-of-spec operation in
the sequence and the only record that became unmanageable.

Consequences: user IDs are bounded to 9 bytes; `ZK.restart()` needs a working
session and so is no help when the service itself has failed; Dropbear on TCP
3718 is the only out-of-band route and needs credentials nobody has.

## Credentials

The user record contains a credential region at bytes 3:35 (32 bytes). Do not
expose its contents in normal tooling.

**The PIN field is PROVEN (PHASE 15): bytes 3:11, NUL-padded ASCII digits.**
Setting `"1234"` on a disposable user stored exactly `31 32 33 34 00 00 00 00`
at 3:11, with the remaining 24 bytes of the region zero; clearing zeroed the
whole region; a name-only update preserved it.

`has_credential_data` therefore does mean "this user has a PIN", proven in both
directions on hardware: a user created without one read back all-zero, non-zero
after setting, zero again after clearing.

Bytes 11:35 have no observed meaning and are always preserved byte-for-byte from
the device's own record, so a write that does not target the PIN cannot disturb
whatever they hold.

Setting or clearing a credential is gated behind
`Capability.WRITE_USER_PASSWORD` -- now SUPPORTED, and still requiring its own
`CLOCKMANAGER_ENABLE_CREDENTIAL_WRITES` unlock.

## Cards

No card field has been identified in the 120-byte record.
`Capability.WRITE_USER_CARD` is UNSUPPORTED and cannot be unlocked.

**PHASE 15 changed the standing of the issue #240 claim without proving it.**
That claim puts a 4-byte little-endian card at bytes 83:87. It was previously
rejected because 83:87 falls inside a 59:96 last-name field. Now that the last
name is shown to be 24 bytes (59:83), **83:87 is a distinct four-byte region**
and the claim is consistent with the layout rather than contradicting it.

It is still not evidence. All four records observed hold zero at 83:87, and no
card was available to enrol. `read_sizes()` reports `cards=2`, but that is
pyzk's guess at an unlabelled `fields[12]` and it did **not** change when a
third user was added, so it does not track users and nothing establishes what it
does count. Every card-related option name (`CardFunOn`, `RFCardOn`,
`~MaxCardCount`) is refused by the device.

The proving test is unchanged: enrol a card on a disposable user via the keypad,
dump the 120-byte record before and after, and diff. Implement nothing until the
offset survives that.

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

## Fingerprint enumeration (PHASE 15, PROVEN)

Enumeration is wired through to the Users screen: the user list shows how many
fingers each person has enrolled, matched to the user record by device UID, and
Settings ▸ Device settings ▸ Device information ▸ Fingerprints lists the slots.
"Unknown" (the device would not enumerate) and "None" (it did, and this person
has none) are different words on that screen and are never conflated. No
template byte leaves `parse_fingerprint_payload`.


The fingerprint store can be **enumerated** on the real NG-MB1. Reading,
writing or enrolling a template still cannot.

- Command: `CMD_DB_RRQ` (7), buffered read, function selector `FCT_FINGERTMP`
  (2).
- Response: 4-byte little-endian total size, then variable-length entries.
- Entry framing: `<HHbb` = total entry size, user UID, finger index, valid
  flag, followed by `size - 6` template bytes.

Observed: 1682 bytes, two entries -- UID 1 finger 6 valid 1 (838 template
bytes) and UID 2 finger 6 valid 1 (828 bytes). **The UIDs matched the 120-byte
user records exactly**, which is the mapping question the whole feature depends
on; having two enrolled users is what made it answerable.

`parse_fingerprint_payload` discards the template bytes at the parser boundary
and returns `FingerprintSlot(device_uid, finger_index, valid, template_bytes)`
-- four integers. Template contents never reach a caller, a log, an export or
the database (`SECURITY.md`).

**`CMD_DB_RRQ` payloads are withheld whole from protocol traces.** The table
read can return the fingerprint store, whose entries are mostly template bytes,
and the function selector that would say which table it was is not part of the
payload. A trace reports the size and nothing else. Over-redacting a diagnostic
is safe; under-redacting discloses a biometric.

`Capability.READ_FINGERPRINT` is SUPPORTED **for enumeration only**, and it is
a read: it needs no write unlock.

## Device options (PHASE 15, PROVEN, read-only)

`CMD_OPTIONS_RRQ` (11) with a NUL-terminated option name returns `Name=Value`.
An unsupported name returns code 4999 harmlessly.

**Implemented** (PHASE 15 wiring): `NGTecoMB1Device.read_device_options()`,
surfaced on Settings ▸ Device settings ▸ Device information and in the
diagnostics trace and export.

Two rules constrain it, both enforced in code:

* The names it will ask for are a **fixed allow-list**
  (`clockmanager.protocol.options.NG_MB1_OPTIONS`). A caller cannot request an
  arbitrary name, and a name matching a credential-shaped fragment (`key`,
  `password`, `pwd`, `secret`, `token`, `comkey`) is refused before a request
  is built, so `ComKey` can never be read into a panel, an export or a log.
* There is **no option write** anywhere in the application.
  `Capability.WRITE_DEVICE_OPTIONS` is UNSUPPORTED and, being unsupported,
  cannot be operator-unlocked. `CMD_OPTIONS_WRQ` is not even defined as a
  constant.

Confirmed answering on this device: `~SerialNumber`, `~DeviceName`,
`~Platform`, `~ProductTime` (2026-01-31 12:18:57), `~OS`, `~PIN2Width` (9),
`~ZKFPVersion` (10), `ZKFaceVersion` (35), `FaceFunOn` (1), `FingerFunOn` (1),
`~UserExtFmt` (1), `CompatOldFirmware` (0), `~IsOnlyRFMachine` (0),
`~MaxUserPhotoCount` (200), `IPAddress`, `NetMask`, `GATEIPAddress`, `DNS`,
`MAC`, `DeviceID` (1), `Language` (69), `VOLUME` (70), `LockOn` (10),
`IdleMinute` (30), `MustEnroll` (0), `MThreshold` (35), `VThreshold` (15),
`RS232BaudRate` (115200), `WorkCode` (0), `~SSR` (1), `~MaxUserCount` (2),
`~MaxAttLogCount` (3), `~MaxFingerCount` (4).

> The `~Max*Count` values are 2, 3 and 4 -- sequential, and nothing like the
> real capacities. They are not capacity fields on this firmware. Capacities
> come from `read_sizes()`.

> **`IPAddress` reports 192.168.1.201 while the device answers at
> 192.168.0.16.** The stored static configuration is not the address in use.
> Never reconnect or scan from this field.

**Refused (4999), so absent on this model:** every workcode, department, group,
shift and timezone option; SMS; user photos; door, lock, alarm, bell and buzzer
options; all the `*Stamp` change counters; every server/cloud push option
(`ServerIP`, `WebServerIP`, `CloudEnable`, `TransFlag`, `TransTimes`,
`Realtime`); `ComKey`.

The absent `*Stamp` counters matter: **the device offers no incremental-sync
handle.** Full re-reads remain the only way to reconcile, which is what the
sync engine already assumes.

## Other data tables (PHASE 15)

`CMD_DB_RRQ` (7) with each function selector:

| Selector | Result on this device |
| --- | --- |
| `FCT_ATTLOG` (1) | 244 bytes, identical to `CMD_ATTLOG_RRQ` |
| `FCT_FINGERTMP` (2) | 1682 bytes, 2 entries -- implemented |
| `FCT_OPLOG` (4) | **528 bytes = 33 records of 16 bytes** -- decoded, PHASE 15 wiring |
| `FCT_SMS` (6) | empty (4-byte payload) |
| `FCT_UDATA` (7) | empty |
| `FCT_WORKCODE` (8) | empty; `WorkCode=0` |

**The device keeps its own operation log.** 33 records, and `read_sizes()`
`fields[10]` (pyzk's `dummy`) also reads 33 -- almost certainly the oplog
count. It shows keypad-side activity, which is a different question from the
application's audit trail: the audit trail records what this application did,
the oplog records what somebody standing at the clock did.

`NGTecoMB1Device.read_operation_log()` reads it, and Settings ▸ Device settings
▸ Device information ▸ Device log shows it.

What is PROVEN about a record is the 16-byte size and the packed ZK timestamp
at bytes 4:8. The rest is INFERRED from the ZKTeco SDK family layout:

```text
  0     operation code        (meaning UNVERIFIED)
  1     reserved              (UNVERIFIED)
  2:4   operator UID, LE uint16
  4:8   packed ZK timestamp   <- PROVEN
  8:14  three LE uint16 parameters (meaning UNVERIFIED)
 14:16  trailing              (UNVERIFIED)
```

Accordingly `parse_operation_log_payload` names no operation code: entries
display as "Operation 5", never as an invented label. A record whose timestamp
does not decode inside the plausible year range keeps `occurred_at=None` and
displays as "Unreadable timestamp", so a misread layout shows itself instead of
costing an operator the other thirty-two records.

## read_sizes() (PHASE 15)

The raw `CMD_GET_FREE_SIZES` (50) response is **112 bytes, 28 int32s**. pyzk
parses 80 of them plus a 12-byte face block and never reads fields 23-27.

With two users enrolled: `users=2 fingers=2 records=6 dummy=33 cards=2
fingers_cap=400 users_cap=200 rec_cap=30000 fingers_av=398 users_av=198
rec_av=29994 faces=2 faces_cap=200`.

Capacities and availability are reliable and are surfaced (PHASE 15 wiring):
`NGTecoMB1Device.read_storage()`, and `DeviceInfo.storage`, which makes every
existing snapshot read "6 of 30,000 used — 29,994 free" instead of "6". **`cards`
is deliberately NOT surfaced**: it is pyzk's guess at an unlabelled field, and
it did not change when a third user was added, so it does not track users and
nothing establishes what it counts. `DeviceStorage` has no field for it.

## Live events (PHASE 15, still UNDETERMINED)

Which of the four body sizes (12/32/36/52) this firmware sends cannot be
established by reading -- it needs a real badge. PHASE 14 captured and parsed a
live event but did not record which layout matched.

The adapter now logs `live_body_bytes` whenever it decodes a live event, so the
next punch anyone captures settles it at no cost.

## Remote recovery (PHASE 15)

`ZK.restart()` (`CMD_RESTART`, 1004) and `ZK.poweroff()` (1005) are ordinary
in-session commands. When the device's ZK service stops completing sessions --
as it did in the UID 901 incident -- **there is no remote reset**, because the
one the protocol offers needs the service that failed. TCP 4370 continues to
accept connections throughout, so reachability proves nothing about health.

Dropbear SSH listens on TCP 3718 and is the only out-of-band route; it needs
device credentials. Ports 22, 80 and 8080 are refused.

A patient reconnect loop is worth having: the device recovered on its own after
about forty minutes and accepted `CMD_RESTART` immediately.

## Relevant external research

The pyzk NGTeco MB1 work/issue is an important reference.
Use current repository references and documented findings as supporting evidence, but verify behavior against the actual MB1 before treating it as production truth.
