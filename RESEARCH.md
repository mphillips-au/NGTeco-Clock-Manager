# Research / Implementation Context

## Device investigation performed

The actual NGTeco NG-MB1 was connected to a local network and experimentally tested.

Confirmed:
- TCP 4370 open
- Dropbear SSH on TCP 3718 (not required for application)
- Linux-based embedded device
- firmware Ver 8.0.4.5-7108-02
- platform ZMM510_TFT
- device identifies as NG-MB1
- `pyzk` 0.9 connection works
- historical attendance works
- `live_capture()` works

## User format discovery

Generic pyzk expects 28/72-byte user structures.
NG-MB1 returned exactly 120 bytes/user.

Verified fields:
- UID
- privilege
- credential/PIN region
- first name
- last name
- user ID

Verified with multiple real users.

## External references worth keeping

Relevant public research includes:
- fananimi/pyzk NGTeco Device Support issue #240
- AMr0ncz/TimeEmployeeManagementCSVConversionNGTeco
- sealum-industries/ngteco-time-editor
- micronesianmacarthur/attendance-log-automation
- related NGTeco payroll/website projects

These are references, not substitutes for real-device verification.

## PHASE 11 investigation notes (2026-09-04, no hardware in session)

- `fananimi/pyzk` issue #240 (NG-MB2, firmware `Ver 8.0.4.5-7108-02`,
  platform `ZMM510_TFT`): compatibility matrix reports `get_templates`
  (bulk fingerprint read, `CMD_DB_RRQ`/`FCT_FINGERTMP`) compatible, but
  `get_user_template` (single read, command 88) incompatible, `enroll_user`
  (`CMD_STARTENROLL`) freezing the device, and `get_users`/`set_user`
  incompatible (120-byte record). `read_sizes`, `get_face_version`,
  `get_face_fun_on`, `get_fp_version` reported compatible; door/lock
  commands not supported. Sibling model, single reporter — supporting
  evidence only, not production truth.
- One January 2026 comment on the same issue claims a 4-byte LE card at
  120-byte-record bytes 83:87, but that range collides with the verified
  last-name region (59:96), the snippet mis-slices neighbouring fields,
  and it reads the card from the whole-buffer offset instead of the
  per-record offset. Treated as a very-low-confidence hypothesis.
- NG-MB1 user manual: 4-in-1 (face, fingerprint, RFID card, PIN);
  200 users / 200 faces / 400 fingerprints / 30,000 records. Confirms the
  modalities exist on the device; says nothing about the TCP protocol.
- pyzk 0.9 has no face-template API (presence flags only) and its
  fingerprint upload path (`save_user_template`) embeds the generic
  72-byte user packet already proven wrong for the MB1.
- Full per-capability evidence map lives in `PROTOCOL.md` under
  "Biometric / card investigation (PHASE 11)". Nothing was implemented.

## PHASE 15 findings (2026-09-05/06, real hardware)

Full report: `phases/PHASE-15.md`. Protocol truth: `PROTOCOL.md`.

What the earlier research got right, wrong and half-right:

- **pyzk issue #240's card claim (4-byte LE at record bytes 83:87)** was
  previously rejected here because 83:87 falls inside a 59:96 last-name field.
  With the last name now shown on hardware to be 24 bytes (59:83), that range
  **is** a distinct region and the claim is consistent with the layout. It is
  still unproven -- no card was available -- and `WRITE_USER_CARD` stays
  UNSUPPORTED. The lesson is that a claim can be right for reasons its author
  could not articulate, and that "it contradicts our layout" is only as strong
  as the layout.
- **Issue #240's `get_templates` (bulk fingerprint read) report was correct**
  for the MB1, not just the sibling MB2: enumeration works, entries are framed
  `<HHbb`, and UIDs map to the 120-byte records. Sibling-model evidence was a
  good pointer here.
- **The NG-MB1 manual's "4-in-1" claim is only half-reachable over TCP.**
  Fingerprint presence and PINs are readable and writable; faces are counted
  but have no template API in pyzk 0.9 and no known command; cards have no
  identified field.
- **The device is far more talkative than the research suggested.**
  `CMD_OPTIONS_RRQ` answers ~33 option names, including `~PIN2Width=9` -- a
  validation limit this project was getting wrong -- and `FCT_OPLOG` returns a
  33-record device-side operation log. Neither appears in any of the external
  references.
- **`read_sizes().cards` should not be trusted.** It is pyzk's guess at an
  unlabelled field; it did not move when a third user was added, and nothing
  establishes what it counts.
- **The device's own `IPAddress` option is stale** (reports 192.168.1.201 while
  answering at 192.168.0.16). Do not use published examples that reconnect from
  it.

The expensive finding: a user ID longer than the device's stated `~PIN2Width`
produced an undeletable record, cost the device both enrolled fingerprint
templates, and took its protocol service down for forty minutes. None of the
external references mention a length limit. Trust the device's own reported
widths over any third-party example.

## Safety

Do not commit real credentials or raw credential-containing fixtures.
