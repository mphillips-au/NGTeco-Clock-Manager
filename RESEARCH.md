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

## Safety

Do not commit real credentials or raw credential-containing fixtures.
