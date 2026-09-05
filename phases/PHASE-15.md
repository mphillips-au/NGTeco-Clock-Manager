# PHASE 15 — Capability investigation against the real NG-MB1

**Date:** 2026-09-05/06
**Device:** NG-MB1, serial NBF6260700048, ZMM510_TFT, Ver 8.0.4.5-7108-02, at
192.168.0.16:4370, comm password 0.
**Branch base:** `origin/phase-14-production-qa` (see "A note on the branch").

This is an investigation, not a feature. The goal was to establish, against the
real clock, what this application could do that it does not do today — and to
say plainly which of those things are proven, which are inferred, and which are
guesses.

> **Read the incident section before running anything against a clock.** One
> write in this session cost the device both of its enrolled fingerprints and
> took its protocol service down for about forty minutes.

---

## A note on numbering

This investigation took the number 15, so the three planned briefs moved up
one: Synology/headless is now `phases/PHASE-16.md`, Web/API `PHASE-17.md`, and
the web frontend `PHASE-18.md`. Their content is unchanged. `STATUS.md` already
flagged the numbering as worth straightening deliberately; this is that.

---

## A note on the branch

This phase was branched from `origin/phase-14-production-qa` rather than from
`main`, because the local `main` did not yet contain the PHASE 14 QA work (the
40-byte record-index fix, the trace-redaction fix, the timestamp bounds and
schema 8) — and the live database in `%LOCALAPPDATA%\NGTecoClockManager` is
already at schema 8, which a schema-7 build refuses to open.

That turned out to be a stale local ref: `origin/main` had already merged the QA
branch as PR #11, with identical content to the branch tip. This phase was
rebased onto it, so `main` is now linear: PHASE 14 QA, then PHASE 15.

The lesson is worth keeping: **check `origin/main`, not the local ref**, before
concluding that work is unmerged.

---

## 1. The incident, first

### What happened

Testing the write path on a disposable account, I wrote a user record with a
**13-character user ID** while the device reports `~PIN2Width=9`. The device
accepted it and read it back intact. After that:

- `CMD_DELETE_USER` on that record (UID 901) was **acknowledged and did
  nothing**, repeatedly, across reconnects and across a reboot.
- The fingerprint store went from **2 templates to 0** (`fingers` 2 → 0,
  `fingers_av` 398 → 400, bulk read 2 entries → 0). Faces stayed at 2 and both
  users' PINs and records were untouched.
- A further write to that UID timed out, and the device then **stopped
  completing ZK sessions entirely** while still accepting TCP on 4370.

### Attribution — honestly

**Not determined.** The over-long user ID is the only out-of-spec operation in
the sequence and the only record that became unmanageable, which makes it the
prime suspect: nine bytes is the device's own stated width, and overrunning it
plausibly reached an adjacent index. But two `CMD_DELETE_USER` calls (UIDs 3 and
900, both well-formed, both successful) happened between that write and the
first observation of `fingers=0`, so a delete cannot be excluded as the
mechanism that actually cleared the store.

Settling it would mean reproducing template loss on a device whose biometrics
are expendable. That is not this device.

### Recovery

TCP stayed open the whole time. A patient retry loop (`CMD_EXIT` then
`CMD_CONNECT`, every 30 s) got a session back after ~40 minutes and immediately
issued `CMD_RESTART` (1004). The device rebooted and has been healthy since.

**Remote reboot is a chicken-and-egg problem.** `ZK.restart()` and
`ZK.poweroff()` are ordinary in-session commands, so the one remote reset the
protocol offers needs the service that had failed. Dropbear SSH is open on TCP
3718 and would be an out-of-band route if device credentials were known; they
are not. There is no other management port (80, 8080 and 22 are all refused).

### Residual damage

| Item | State |
| --- | --- |
| UID 1 (Dean) record | **Byte-identical** to the pre-write snapshot, PIN included |
| UID 2 (Erin) record | **Byte-identical** to the pre-write snapshot, PIN included |
| Attendance | 6 records, unchanged |
| Faces | 2, unchanged — both users can still clock in by face |
| **Fingerprints** | **0. Both enrolments lost; physical re-enrolment required** |
| UID 901 `ZZTEST-LONGID` | **Still present and undeletable over the protocol** |

The leftover user has privilege 0, no PIN, no fingerprint and no face, so it
cannot clock in by any means. It is untidy, not dangerous.

### What was changed so it cannot recur

`build_user_record` now refuses a user ID over 9 bytes and a last name over 23,
with the incident named in the error message, and `UserDraft.validate` refuses
the same before a form can offer them. The check lives in the builder as well as
the domain layer so a caller that builds a record directly cannot skip it.

---

## 2. Evidence map

Same columns `PROTOCOL.md` uses. Confidence is one of **PROVEN HERE** (observed
on this device this session), **PROVEN (PHASE 14)**, **SIBLING** (reported on a
related model), **INFERRED** (read from pyzk source), **GUESS**.

### 2.1 Writes — the whole of section A of the brief

| Question | Answer | Evidence | Confidence | Reversible | Test |
| --- | --- | --- | --- | --- | --- |
| Does the device accept our 120-byte record? | **Yes** | `CMD_USER_WRQ` (8) + `CMD_REFRESHDATA` (1013) created UID 3 and UID 900 and applied every update | PROVEN HERE | yes (delete) | `test_phase15_capabilities.py` |
| Update (rename)? | **Yes** | Last name changed and read back | PROVEN HERE | yes | ✓ |
| Privilege change? | **Yes**, both directions | 0 → 14 → 0 on UID 3, verified each way | PROVEN HERE | yes | ✓ |
| Delete? | **Yes**, for well-formed records | `CMD_DELETE_USER` (18), `<H` UID, removed UIDs 3 and 900; absence confirmed by re-read | PROVEN HERE | no | ✓ |
| Does it accept an **application-chosen UID**? | **Yes** | A record built with UID 900 — above the 200-user capacity — was stored, read back as UID 900 and deleted cleanly | PROVEN HERE | yes | — |
| Is the PIN at bytes 3:11? | **Yes** | Setting `"1234"` stored exactly `31 32 33 34 00 00 00 00` at 3:11 with the other 24 bytes of the region zero | PROVEN HERE | yes | ✓ |
| Does `has_credential_data` mean "a PIN is set"? | **Yes** | A user created with no PIN read back all-zero; after set, non-zero; after clear, zero again. Both enrolled users show exactly **4 ASCII digits** at 3:7 and nothing else | PROVEN HERE | n/a | ✓ |
| Does PRESERVE keep the PIN? | **Yes** | Rename with `CredentialAction.PRESERVE` left the credential present | PROVEN HERE | yes | ✓ |

**The credential region is settled.** It is an 8-byte NUL-padded ASCII PIN
followed by 24 bytes with no observed meaning. Both real users have 4-digit
PINs. (Stated as a classification — no digit of either PIN was read, printed or
stored anywhere in this work.)

### 2.2 The defect that hid all of the above

Every one of those writes **succeeded on the device and then failed
verification**, because `_compare_records` demanded byte equality outside the
credential region. The MB1 does not store the bytes it is given:

1. **The device owns byte 87.** It comes back `0x01` on every stored record
   whatever we send. Byte 90 is `0x01` on the two biometrically-enrolled users
   and `0x00` on freshly created ones — suggestive of an enrolment flag, but
   **UNVERIFIED**, since no biometric was enrolled on a test user.
2. **The device does not zero-fill field tails.** Bytes after a field's NUL keep
   whatever the slot held before. A record written into a slot that previously
   held "Dean" came back with `n` still at offset 38. Erin's live record carries
   `nis` at 65:68 — the tail of "Gianginis" — and the diagnostics trace shows it:
   `53 74 69 6c 6f 00 6e 69 73` = `"Stilo\0nis"`.

This is why `WRITE_USERS` could never graduate: the adapter was asserting a
property the device never promised. Verification now compares the **decoded
fields** — UID, user ID, first name, last name, privilege — plus credential
presence. That is both correct and a stronger promise to the operator.

**Re-run after the fix, on hardware:** create → rename → privilege 0→14→0 → PIN
set → rename with preserve → PIN clear → delete, every step verified by the
adapter's own read-back. All passed.

### 2.3 Record layout, refined

```text
  0:2    UID, little-endian uint16
  2      privilege (0 Employee, 14 Admin)
  3:11   PIN, NUL-padded ASCII            <- PROVEN HERE
 11:35   unknown, always preserved
 35:59   first name (24 bytes)
 59:83   last name  (24 bytes usable)     <- 23 chars + NUL, PROVEN HERE
 83:87   unknown, zero on all four records observed
 87      device-owned flag, always 0x01   <- PROVEN HERE
 88:90   unknown, zero
 90      0x01 on biometrically-enrolled users only  <- UNVERIFIED
 91:96   unknown, zero
 96:120  user ID (24-byte field, 9 bytes usable)    <- PROVEN HERE
```

A 30-character last name came back truncated to 23 characters, with the 24th
byte zeroed by the device and byte 88 overwritten. The parser reads 59:96 and
splits on NUL, which stays correct; the **writable** budget is what changed.

### 2.4 Cards — the issue #240 claim

**Not settled, but the claim is now plausible rather than contradictory.**

`PROTOCOL.md` previously rejected the "4-byte LE card at bytes 83:87" claim
because 83:87 falls inside a 59:96 last-name field. With the last name now shown
to be 24 bytes (59:83), **83:87 is a distinct four-byte region** and the claim
lines up with the layout. All four records observed hold zero there.

What is still missing is a card. `read_sizes()` reports `cards=2`, but that
field is pyzk's guess at `fields[12]` and it **did not change when a third user
was added**, so it does not track users; whether it counts cards is unknown. No
card-related option name answered (`CardFunOn`, `RFCardOn`, `~MaxCardCount` all
refused).

`WRITE_USER_CARD` stays **UNSUPPORTED**. The proving test is unchanged and still
needs an actual card: enrol one on a disposable user via the keypad, dump the
record before and after, and diff. No card was available.

### 2.5 Attendance

| Question | Answer | Evidence |
| --- | --- | --- |
| Record size | **40** | 6 records → 244-byte payload = 4-byte prefix + 6 × 40. Corroborates PHASE 14 |
| Leading uint16 | **Record index, not UID** | Indices ran 1..6 across two users; index 6 belongs to user `"2"`, and users 3, 4, 5 do not exist. Stronger than PHASE 14's single-user sample |
| Trailing 8 bytes | All zero on every record | — |
| `status` values | 1 and 15 observed | — |

**`status` is very likely the verification method.** 1 and 15 are the standard
ZKTeco codes for fingerprint and face, and this device has exactly one
fingerprint and one face per user; the split (three of each) fits people using
both. It is **not proven** — it needs one punch made deliberately with a known
modality. Until then `status` stays opaque, which is the correct default and
costs nothing.

### 2.6 Fingerprints — the one biometric win

**Enumeration works and is now implemented.** `CMD_DB_RRQ` (7) with
`FCT_FINGERTMP` (2), buffered read, 4-byte total-size prefix, entries framed
`<HHbb` = entry size, user UID, finger index, valid flag, then `size - 6`
template bytes.

Observed: two entries, UID 1 finger 6 valid 1 (838 template bytes) and UID 2
finger 6 valid 1 (828 bytes). **The UIDs matched the 120-byte user records
exactly** — the mapping question the brief raised is answered, and having two
users is what answered it.

`READ_FINGERPRINT` graduates to **SUPPORTED, for enumeration only.**
`parse_fingerprint_payload` discards template bytes at the parser boundary and
returns `FingerprintSlot(device_uid, finger_index, valid, template_bytes)` —
four integers, no bytes anywhere.

> **A trap I walked into and fixed.** Wiring this into the diagnostics trace
> initially routed the raw payload — which is mostly template bytes — through
> `redact_payload_preview`, which only knew about user data and would have
> hex-dumped it into the trace, the export and the log. `CMD_DB_RRQ` payloads
> are now withheld whole, with a test.

Everything else fingerprint-related is unchanged and stays out: single template
read (command 88) is reported incompatible on the sibling model, `enroll_user`
freezes it, and `save_user_template` embeds the 72-byte packet already proven
wrong here.

### 2.7 Faces

**Nothing to implement from.** `FaceFunOn=1` and `ZKFaceVersion=35` read
cleanly, and the face count is reported, but pyzk 0.9 has no face-template API
and no command, payload or structure is known. `READ_FACE` stays UNVERIFIED with
that reason recorded. This is a dead end until someone captures the vendor
software's traffic.

### 2.8 `set_time`

**Still UNVERIFIED, deliberately.** The device clock was correct to 31 seconds,
so there was no safe pretext to change it. Writing a clock is not reversible in
any meaningful sense — every punch afterwards carries it — and exercising it for
its own sake on a production attendance device is not a good trade. The
operation is one `CMD_SET_TIME` call away when there is a real reason.

### 2.9 Live events

**Undetermined.** Which body size (12/32/36/52) this firmware sends cannot be
learned by reading; it needs a badge, and nobody was on site. PHASE 14 captured
a real live event and parsed it correctly, but did not record which layout it
matched.

Rather than guess, the adapter now **logs the observed body size** whenever a
live event is decoded. The next punch anyone captures answers it, at no cost.

---

## 3. The untapped surface (brief section C)

### 3.1 Device options — the big finding

`CMD_OPTIONS_RRQ` (11) with a NUL-terminated option name returns `Name=Value`.
It is read-only, safe, fast, and **the application touches none of it.** An
unsupported name returns code 4999 and is harmless.

Confirmed working on this device:

| Option | Value | Worth having? |
| --- | --- | --- |
| `~SerialNumber` | NBF6260700048 | already have |
| `~DeviceName` / `~Platform` | NG-MB1 / ZMM510_TFT | already have |
| `~ProductTime` | 2026-01-31 12:18:57 | manufacture date — nice for asset records |
| `~PIN2Width` | **9** | **yes — this is a validation limit we were getting wrong** |
| `~ZKFPVersion` / `ZKFaceVersion` | 10 / 35 | yes — biometric algorithm versions |
| `FingerFunOn` / `FaceFunOn` | 1 / 1 | yes — which modalities exist |
| `~UserExtFmt` | 1 | relevant to record layout |
| `~MaxUserPhotoCount` | 200 | user photos may exist; untested |
| `IPAddress` / `NetMask` / `GATEIPAddress` | **192.168.1.201** / 255.255.255.0 / 0.0.0.0 | **yes, with a warning** |
| `MAC` | 00:17:61:11:b9:f6 | yes — stable device identity for discovery |
| `DeviceID` | 1 | yes — multi-device addressing |
| `VOLUME` / `Language` / `IdleMinute` | 70 / 69 / 30 | low value |
| `LockOn` | 10 | door-lock duration; the device has some door concept |
| `MThreshold` / `VThreshold` | 35 / 15 | biometric match thresholds |
| `WorkCode` | **0** | workcodes are **off** on this device |
| `MustEnroll` / `CompatOldFirmware` / `~SSR` / `~IsOnlyRFMachine` | 0 / 0 / 1 / 0 | low value |
| `RS232BaudRate` | 115200 | none |

> **`IPAddress` reports 192.168.1.201 while the device is reachable at
> 192.168.0.16.** The stored static configuration is not the address in use.
> Never use this field to reconnect or to identify a device on the LAN — it
> would send a discovery scan to the wrong subnet.

**Refused (code 4999) — these features look absent on this model:** every
workcode, department, group, shift and timezone option; SMS; user photos;
door/lock/alarm/bell/buzzer; all the `*Stamp` change-tracking counters; every
server/cloud push option (`ServerIP`, `WebServerIP`, `CloudEnable`,
`TransFlag`/`TransTimes`/`Realtime`); `ComKey`.

The absent `*Stamp` counters matter: **there is no incremental-sync handle.**
Full re-reads stay the only option, which is what the sync engine already
assumes.

### 3.2 Other data tables (`CMD_DB_RRQ`)

| Selector | Result | Verdict |
| --- | --- | --- |
| `FCT_FINGERTMP` (2) | 1682 bytes, 2 entries | **implemented this phase** |
| `FCT_OPLOG` (4) | **528 bytes = 33 records × 16** | **the most interesting untapped read** |
| `FCT_ATTLOG` (1) | 244 bytes, same as `CMD_ATTLOG_RRQ` | already have |
| `FCT_WORKCODE` (8) | empty (4 bytes) | supported, unused, `WorkCode=0` |
| `FCT_SMS` (6) | empty | supported, unused |
| `FCT_UDATA` (7) | empty | supported, unused |

**The device keeps its own operation log.** 33 records, and `read_sizes()`
`fields[10]` (pyzk's `dummy`) is also **33** — almost certainly the oplog count.
Records are 16 bytes with a packed ZK timestamp at bytes 4:8. Not decoded here.

This is a real gap: the application audits what *it* did, and the device
separately records what was done *at the keypad* — enrolments, deletions, admin
menu access. Nobody can currently see the latter.

### 3.3 `read_sizes()` in full

The raw `CMD_GET_FREE_SIZES` response is **112 bytes (28 int32s)**; pyzk parses
80 of them plus a 12-byte face block and **never looks at fields 23-27**.

Observed with 2 users: `users=2 fingers=2 records=6 dummy=33 cards=2
fingers_cap=400 users_cap=200 rec_cap=30000 fingers_av=398 users_av=198
rec_av=29994 faces=2 faces_cap=200`.

Capacities are worth surfacing — "398 of 400 fingerprint slots free" is
operator-useful and free. `cards` should not be shown until it is understood.

---

## 4. Prioritised roadmap

Effort is rough developer-days for someone with this codebase loaded.

### Do first — this session's debt

| # | Item | Why | Effort | Risk |
| --- | --- | --- | --- | --- |
| 1 | **Re-enrol both fingerprints at the device** | Physical; only the operator can do it | — | none |
| 2 | **Remove `ZZTEST-LONGID` from the device keypad** | Protocol deletion does not work on it; see §5 | — | none |

### High value, high confidence

| # | Item | Evidence | Effort | Notes |
| --- | --- | --- | --- | --- |
| 4 | **Turn on user writing for real** | PROVEN HERE end to end | 1 | Mostly documentation and an operator decision — the code is done and verified. Keep the env gates |
| 5 | **Device settings panel from `CMD_OPTIONS_RRQ`** | PROVEN HERE, read-only | 2 | Highest value per unit of risk in this document. Firmware, MAC, algorithm versions, capacities, thresholds, PIN width. Reads only — writing options is a separate, unproven thing |
| 6 | **Surface capacity/usage** (`users_cap`, `fingers_av`, `rec_av`) | PROVEN HERE | 0.5 | "6 of 30000 records used". Trivial; already in `read_sizes()` |
| 7 | **Fingerprint enrolment status per user in the Users view** | PROVEN HERE (enumeration) | 1 | The adapter method and the trace step exist. "Has a fingerprint / has a face / has a PIN" is a genuine operational question |
| 8 | **Scheduled/background sync as a first-class thing** | No new protocol work | 2 | Section D. The engine already has `background` and `recovery` sources |
| 9 | **Australian payroll export (STP-shaped CSV, Xero/MYOB)** | No new protocol work | 3-5 | Section D. Highest business value in the whole document; needs the operator's actual payroll target, not a guess |

### Worth doing, moderate confidence

| # | Item | Evidence | Effort | Notes |
| --- | --- | --- | --- | --- |
| 10 | **Decode `FCT_OPLOG`** | PROVEN HERE that it reads; layout INFERRED | 1-2 | Read-only and safe to iterate on. Would show keypad-side enrolments and deletions |
| 11 | **Prove `status` = verification method** | Strong inference, unproven | 0.1 | One deliberate punch. Cheapest high-value item here — ask the operator to badge once by finger and once by face |
| 12 | **Settle the live-event body size** | Logging is in place | 0 | Answers itself on the next punch |
| 13 | **Multi-device consolidation** | No new protocol work | 3 | `DeviceID` and `MAC` are readable; the architecture is already multi-device |
| 14 | **Headless Synology service** (`phases/PHASE-16.md`) | No new protocol work | 3-5 | Unchanged by this investigation; the core is already PySide6-free |

### Traps — high apparent value, do not be tempted

| Item | Why it is a trap |
| --- | --- |
| **Card read/write** | The 83:87 hypothesis now *fits* the layout, which makes it far more tempting and no better evidenced. A wrong offset writes into bytes the device owns. Do not implement before a physical card enrolment diff |
| **Fingerprint enrolment (`CMD_STARTENROLL`)** | Reported to freeze the sibling model, and freezing mid-write is how you lose a device with nobody on site |
| **Fingerprint template upload** | `save_user_template` embeds the 72-byte packet already proven wrong for this record format |
| **Face anything** | No command exists to build on. Effort is unbounded, not large |
| **`set_time`** | Cheap to implement, irreversible in effect, and every subsequent punch inherits a mistake. Only with a reason |
| **`clear_attendance` / factory reset** | Correctly absent. Keep them absent |
| **Trusting `IPAddress`** | It is wrong on this device today |
| **Trusting `cards`** | pyzk's guess at an unlabelled field; it does not track users, and nothing establishes what it does count |

---

## 5. What I could not determine, and what it would take

| Question | Blocker | What it needs |
| --- | --- | --- |
| **Where the card lives** | No physical card | Enrol a card on a disposable user at the keypad; dump the 120-byte record before and after; diff |
| **What `status` means** | Nobody on site | One punch by fingerprint and one by face, then read the log |
| **Which live-event layout this firmware sends** | Nobody on site | Any punch, now that the size is logged |
| **What bytes 90, 83:87 and 11:35 hold** | Needs state we cannot create remotely | Enrol a biometric on a disposable user and diff the record |
| **Whether `cards` counts cards** | Same | Same |
| **What actually destroyed the fingerprints** | Needs a device with expendable biometrics | Reproduce the long-ID write, then the deletes, separately |
| **How to delete UID 901 over the protocol** | The record appears to be indexed wrongly | Keypad deletion. I stopped after `CMD_DELETE_USER` was acknowledged-but-ineffective four times, and a rewrite attempt is what wedged the device the first time |
| **Whether an option can be *written*** | Not attempted | `CMD_OPTIONS_WRQ` on a harmless option (e.g. `VOLUME`) with the value restored afterwards. Deliberately out of scope after the incident |
| **Face templates** | No known command | Traffic capture from the vendor software |

---

## 6. Tooling findings

`DiagnosticsService` did most of this work and is genuinely good: it produced
the timed connection report, the TX/RX capture, the redacted 120-byte records
(which is where the "Stilo\0nis" residue is plainly visible), the raw attendance
payload and the capability report.

Where it could not help, and why that matters:

- **It has no write path, by design.** The entire write investigation had to run
  through the adapter directly. That is the right design — investigation tooling
  must not become a casual route to destructive actions — but it means the write
  path has no supported way to be exercised except a hand-written script. The
  opt-in integration suite is the intended home; it should grow the cases proven
  here.
- **It showed no device options.** The whole `CMD_OPTIONS_RRQ` surface — the
  single richest safe read on this device — was invisible to it. Roadmap item 5.
- **It surfaced no capacities.** `DeviceInfo` carries four counters; `read_sizes()`
  returns twenty-eight fields.
- **It had no fingerprint step** until this phase added one.
- **Its redaction was user-data-shaped only**, and adding a biometric read to
  the trace was enough to route template bytes at it. Fixed, with a test.

---

## 7. Changes made in this phase

Protocol/domain:

- `_compare_records` compares decoded fields, not bytes — the fix that makes the
  write path usable at all.
- `USER_ID_WRITABLE_BYTES = 9`, `USER_LAST_NAME_WRITABLE_BYTES = 23`, enforced in
  both `build_user_record` and `UserDraft.validate`.
- `USER_PASSWORD_CANDIDATE_SLICE` → `USER_PASSWORD_SLICE`; it is no longer a
  candidate.
- `USER_DEVICE_FLAG_OFFSETS` documents bytes 87 and 90.
- `FingerprintSlot`, `parse_fingerprint_payload`, `read_fingerprint_slots`.
- `redact_payload_preview` withholds `CMD_DB_RRQ` payloads whole.
- Live-event body size is logged when observed.

Capability model:

- New `Support.OPERATOR_LOCKED` and `DeviceCapabilities.locked()`, separating
  *the device supports this* from *this installation may do it*. Without it,
  graduating `WRITE_USERS` to SUPPORTED would have silently switched writing on
  everywhere — the capability state was doubling as the safety gate.
- `WRITE_USERS`, `DELETE_USERS`, `WRITE_USER_PASSWORD`, `READ_FINGERPRINT` →
  SUPPORTED, each with its hardware evidence in the reason.
- `SET_TIME`, `READ_FACE` stay UNVERIFIED, with better reasons.
- `WRITE_USER_CARD`, `CLEAR_ATTENDANCE` stay UNSUPPORTED.

Tests: `tests/unit/test_phase15_capabilities.py`, 37 tests pinning every
graduation, both field budgets, the comparison's tolerance *and* its strictness,
the fingerprint framing, and the fact that no template byte can reach a preview.
