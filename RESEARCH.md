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

## Safety

Do not commit real credentials or raw credential-containing fixtures.
