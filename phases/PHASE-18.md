# PHASE 18 — Site Agent and Hosted Portal (design brief)

> **Status: design only, awaiting operator review.** Written 2026-09-11. No
> application code was changed and the clock was not contacted while writing
> it. The build steps are PHASE 18A–18H (section 11). The web frontend,
> previously this number, is now `phases/PHASE-19.md` and depends on this
> phase.

## Goal

Replace the on-site web design with a **centrally hosted portal** and a
small **site agent** on each clock's network. The agent is the only thing
that talks to a clock. It makes outbound HTTPS connections only, so no port
is opened into any office.

## Read (for the 18A–18H build sessions)

- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md (the top entry)
- this brief
- SECURITY.md
- PROTOCOL.md: "Write protocol", "Remote recovery", "Timestamp range"
- TESTING.md

---

## 0. Decisions, answers and assumptions

### Recorded decision (2026-09-10)

Hosted portal plus a site agent (option B). **Rejected:** one on-site box
running `--serve`, `--api-serve` and the web UI. The on-site box needs an
inbound port or a VPN for anyone off site. It also puts employee data and the
web attack surface on the clock's LAN, and each site needs its own server.

### Operator answers (2026-09-11)

| Question | Answer | Consequence in this brief |
| --- | --- | --- |
| Who is the portal for? | This business only, **multi-tenant-ready** | `tenant_id` on every central table and in agent identity from day one. No signup, billing or self-service onboarding. |
| Hosting | **Own VPS/server** | Docker Compose: reverse proxy with automatic TLS, app and Postgres on one VPS. The operator owns patching, backups and monitoring (section 12, R8). |
| Agent host | **Windows, Docker and Linux** | All three are packaged in 18C from one codebase. |
| Portal login | **Local accounts + TOTP** | The existing `AuthService` and roles, plus server-side sessions, lockout and TOTP. No SSO for now. |

### Assumptions (stated instead of asked; correct any that are wrong)

- **Scale within two years:** at most 5 sites, 10 clocks, 200 employees and
  a few thousand punches a day. Nothing here needs horizontal scaling. One
  VPS process and one Postgres are enough.
- **Clocks:** NG-MB1 (ZMM510_TFT, Ver 8.0.4.5-7108-02) only. Other models
  come in through the existing device interface when needed.
- **Site network:** outbound HTTPS (TCP 443) is allowed. A site may force an
  HTTP proxy, which the agent must support.
- **Agent host:** always on, NTP-synced, on the same LAN as its clocks.
- **Data residency:** the VPS is in an Australian region (see D5).
- **Record keeping:** attendance and employee records are kept for 7 years
  (Fair Work record-keeping; confirm with the business's accountant).

---

## 1. Who owns what

**Rule:** the agent owns every byte that crosses TCP 4370. The server owns
people, time and money. The server never imports `clockmanager.protocol`,
and a test pins that, the way the headless package is pinned today.

| Concern | Agent (site) | Server (hosted) |
| --- | --- | --- |
| Clock connection, sessions, live capture | **owns** | never |
| Device secrets (communication password) | **owns**, encrypted at rest | only `has_communication_password` |
| Local buffer (outbox of punches, command journal) | **owns** | — |
| Sync engine (read, reconcile, dedupe locally) | **owns** | re-dedupes on ingest |
| Device-user snapshot (names, privilege, flags) | reads | stores a copy, with no credential fields |
| Employees, links, pay schedules, timesheets, reports | — | **owns** |
| Portal accounts, roles, sessions, TOTP | — | **owns** |
| Audit | local append-only log of device actions, shipped up | **system of record** |
| Write gates | **owns and enforces** | can see them, cannot change them |
| Command policy (who may ask) | re-checks the allow-list | **owns** roles and permissions |

### Package map

| Package | Goes to | Notes |
| --- | --- | --- |
| `protocol/` (mb1, records, builders, capabilities, options, discovery, trace, mock) | **Agent** | Never deployed to or imported by the server. The mock stays for agent tests. |
| `sync/engine.py`, `sync/sources.py` | **Shared** | Pure. The agent uses the engine for local dedupe; the server uses the same logic on ingest. |
| `sync/keys.py` | **Shared** | Gains a v2 key (section 2.3). v1 stays for existing SQLite rows. |
| `domain/` | **Shared** | Models, auth roles, payroll, reports, users (`UserDraft.validate` runs on both sides). |
| `security/` (passwords, redaction) | **Shared** | Redaction runs on the agent before anything leaves the site. |
| `diagnostics/logging_setup.py` | **Shared** | |
| `persistence/` | **Shared code, two schemas** | Agent: a small SQLite buffer schema. Server: the full model on Postgres (section 6). The existing SQLite schema and migrations stay for the desktop app. |
| `headless/runner.py`, `headless/health.py` | **Agent** | The runner becomes the agent runtime. Health stays bound to localhost. |
| `services/sync.py` | **Agent** (device half) / **Server** (ingest) | `_read_device` and the device-facing half stay on the agent. Storing a batch becomes a server ingest service built on the same `plan_inserts`. |
| `services/devices.py` | **Split** | Connect, inspect, discovery and capabilities go to the agent. The device registry (name, site, status) goes to the server. |
| `services/users.py` | **Agent** executes / **Server** requests | The write path stays exactly where it is, on the agent. The server only creates command requests. |
| `services/employees.py`, `timesheets.py`, `reports.py` | **Server** | |
| `services/auth.py` | **Server** | Plus TOTP, lockout and sessions. The agent has no human login. |
| `services/audit.py` | **Both** | Same actions and redaction, two stores. |
| `services/diagnostics.py` | **Agent** | Protocol traces never leave the site (section 9). |
| `services/backup.py` | **Desktop** | The server backs up with `pg_dump` (section 7). The agent buffer is rebuildable from the clock. |
| `services/application.py` | **Split** | `AgentContext` and `ServerContext` replace one `ApplicationContext`. |
| `api/` | **Server** | The seed of the portal API. Routes that touch a device (`/sync`, `/inspect`, `/test`, `/discovery/*`, device-user writes) become command-issuing routes. In-memory tokens become Postgres-backed sessions. |
| `gui/`, `windows.py` | **Desktop** (`windows.py` also agent) | `windows.py` already calls Win32 through ctypes, so DPAPI can be added the same way with no new dependency. |
| `config.py`, `__main__.py` | **Split** | New entry points `clockmanager-agent` and `clockmanager-server`. |

Install extras follow the split: `clockmanager[agent]` (pyzk, SQLAlchemy,
`cryptography`), `clockmanager[server]` (FastAPI, uvicorn, SQLAlchemy,
psycopg, Alembic, `cryptography`), `clockmanager[gui]`.

### Existing code that breaks the one-owner rule (found while writing this)

These are real today and must be fixed before an agent exists (18A):

1. **`--serve --live` holds two sessions per clock at once.** The live worker
   keeps a session open (`headless/runner.py`, `_LiveWorker.run` via
   `open_device`). Each periodic pass opens another session
   (`services/sync.py`, `_read_device` via `connected`). Nothing serialises
   the two, so it is one process but two concurrent sessions.
2. **The single-instance lock covers only the GUI, and only per data folder.**
   `gui/single_instance.py` is claimed only when launching the GUI
   (`__main__._claim_gui_instance`). `--serve` and `--api-serve` take no
   lock, and a different `--data-dir` gives a different lock. The GUI,
   `--serve` and `--api-serve` on one PC can all own the same clock.
3. **`--api-serve` performs device I/O itself.** `POST /api/devices/{id}/sync`,
   `/inspect`, `/test` and discovery run inside the API process, so an API
   beside `--serve` is a second owner.

---

## 2. Agent-to-server protocol

### 2.1 Transport: HTTPS request/response, long-polling for commands

| | HTTPS + long-poll (**chosen**) | WebSocket | SSE + POST |
| --- | --- | --- | --- |
| Outbound-only, through NAT and forced proxies | Yes, plain HTTPS | Often breaks on TLS-inspecting or old proxies | Proxies buffer the stream |
| Command latency | Under a second (the server holds the poll open) | Under a second | Under a second |
| Delivery guarantees | Every message is a request with a response; retries are idempotent | Needs its own ack layer on top | Two channels, acks for both |
| Server state | Stateless per request; one in-process wakeup per agent | Long-lived socket per agent, sticky routing | Long-lived stream per agent |
| Auth | Checked on every request | Only at connect; token expiry mid-socket needs handling | Mixed |
| Testing | FastAPI `TestClient`, as today | Harder | Harder |
| New dependency | None on the server; `httpx` is already a dev dependency and stdlib `urllib` also works | A WebSocket library | None |

At this volume (a heartbeat a minute, punches in tens a minute at peak),
long-polling costs nothing noticeable, and it is the option most likely to
work at every site. **Revisit WebSocket only if** the live view needs
sub-second fan-out to many browsers from many agents. That is a browser-side
concern (PHASE 19) and does not change the agent link.

- Base path `https://<portal>/agent/v1/`. The version is in the path, and
  the server accepts the current version and the previous one.
- TLS 1.2+ with the OS trust store. No certificate pinning by default,
  because sites with TLS inspection would break (R13).
- JSON bodies, gzip allowed. Every request carries `X-Agent-Version` and a
  request ID for log correlation.

### 2.2 Message types

| Endpoint | Direction and purpose | Frequency |
| --- | --- | --- |
| `POST /enrol` | Pairing code + agent public key → agent ID (pending approval) | Once |
| `POST /token` | Signed assertion → 1-hour access token | Hourly |
| `POST /heartbeat` | Agent status up; server time and desired settings down | Every 60 s |
| `POST /punches` | A batch of punches for one device | On new punches, plus replay |
| `POST /devices` | Device inventory: serial, model, platform, firmware, capacity, capability states | On connect and on change |
| `POST /device-users` | Full device-user snapshot for one device, with a snapshot hash | When the hash changes |
| `GET /commands/next?wait=25` | Long-poll: one command envelope (200) or nothing (204) | Continuous |
| `POST /commands/{id}/result` | Outcome with read-back evidence | Per command |
| `POST /audit` | Local audit events (device actions, refusals, gate state at start) | Batched |
| `POST /credentials/rotate` | New public key, signed by the old and new keys | Every 90 days |

**Heartbeat up:** agent version, OS, uptime; the write-gate state (for
display only); and per device: reachable, last successful pass, last error
class, consecutive failures, current backoff, quarantined, clock offset,
record usage ("6 of 30,000"), outbox depth and oldest unsent age, live
capture on or off.

**Heartbeat down:** `server_time`; `desired` operational settings (poll
interval, live capture on/off, batch size), which the agent clamps to local
limits; `commands_pending`; `update_available` (a version number, never a
URL, section 7); `rotate_key`. The server can never set a gate, an
allow-list, a device address, a device secret, the server URL or the update
source. The schema has no fields for them (section 5.3).

**Punch batch** (at most 500 events or 256 KB):

```json
{
  "batch_id": "0192f3c4-…",               // UUIDv7, logged and echoed back
  "device_serial": "NBF6260700048",
  "device_clock_offset_s": 31,             // measured on the pass that read these
  "events": [{
    "seq": 1042,                           // agent outbox sequence, per device
    "event_key": "9f2c…",                  // v2 key, section 2.3
    "device_uid": 2, "user_id": "2",
    "occurred_at_local": "2026-09-10T07:58:12",   // verbatim, naive device time
    "punch": 0, "status": 15,
    "source": "live",                      // SyncSource value
    "captured_at": "2026-09-10T07:58:13Z"  // agent UTC
  }]
}
```

Response: `{accepted, duplicate, rejected: [{seq, reason}], acked_through_seq}`.

Every agent-to-server schema is a Pydantic model with `extra="forbid"`, and
a test pins each one's exact field set, like the existing backup-export key
test. A field that could carry a PIN, card number, template, raw record or
protocol trace cannot be added without that test failing.

### 2.3 Idempotency: event key v2

`sync/keys.py` hashes `device_id|user_id|occurred_at|punch|status`, and
`device_id` is a **local SQLite integer**. The same punch read by two
installations (a desktop and an agent, or an agent before and after a
reinstall) gets two different keys, so central dedupe would fail exactly
when it is needed most.

**v2 key** (added next to v1 in 18A; v1 is unchanged, so existing rows and
migrations are unaffected):

```text
sha256("v2|" + SERIAL + "|" + user_id + "|" + YYYY-MM-DDTHH:MM:SS + "|" + punch + "|" + status)
```

- `SERIAL` is the device's own serial number, trimmed and upper-cased. The
  MB1 reports one (`NBF6260700048`, verified). **A device that cannot report
  a serial is not uploaded**, and the agent reports "no stable identity".
  Inventing an identity would break dedupe silently.
- The time is the device's naive local wall time at second precision.
  `normalise_for_key` rules are unchanged.
- **The server recomputes the key** from the fields and rejects a mismatch
  per event. It never trusts a key it did not compute.
- Postgres enforces `UNIQUE (tenant_id, device_id, event_key)` plus the
  natural-key constraint, and inserts with `ON CONFLICT DO NOTHING`.
  Resending a batch is therefore always safe, so there is no separate
  batch-level dedupe table.

Commands are idempotent by `command_id`: the agent journal refuses to
execute an ID twice, and the server accepts the first result for an ID and
ignores repeats.

### 2.4 Ordering

- Correctness **does not depend on order.** Timesheets are computed from
  `occurred_at_local`, and dedupe is by key. A live punch and the same punch
  from a later full re-read are the same row.
- The agent still sends oldest-first, one batch in flight per agent, so the
  portal fills forward and the ack is simple. `acked_through_seq` is the
  highest seq below which everything is stored or rejected. The agent marks
  those rows acked.
- **A late punch for a closed pay period** (after an outage) is stored, never
  dropped. If that period has already been exported, it raises a "late
  punch" exception for a person to handle instead of silently changing a
  total that was already paid. This needs pay-period locking, which PHASE 19
  or later adds.

### 2.5 Backfill after an outage

There are three buffers, and the first is the clock itself: it keeps up to
30,000 records (`rec_cap`, verified) and nothing in this application clears
it (`clear_attendance` does not exist, deliberately).

| Outage | What happens |
| --- | --- |
| Internet or server down | The agent keeps collecting into the outbox. On reconnect it replays oldest-first in batches, with the existing 30 s-doubling backoff capped at 10 min, plus jitter. |
| Clock unreachable | Today's recovery: the next successful pass re-reads the whole device log, so anything missed arrives then (`SyncSource.RECOVERY`). |
| Agent host off | The clock keeps the punches. The first pass after start re-reads everything. |
| Agent reinstalled or its data lost | The new agent re-reads the whole log and uploads it. v2 keys make every duplicate a no-op on the server. |
| Server restored from an older backup | The portal issues `device.resync_full` (a read-only command). The agent re-uploads the whole device log and its retained acked outbox (90 days). |

The clock log fills in about 17 months at 20 employees making four punches a
day. What the MB1 does when it is full is **UNKNOWN** (R6).

---

## 3. Enrolment and identity

### Pairing flow

1. An admin opens **Sites → Add agent** in the portal. The server issues a
   pairing code: 12 Crockford base32 characters (60 bits), shown as
   `K7QD-9MXT-2FWA`. It is bound to one tenant and site, single use, valid
   for 15 minutes, and stored only as a SHA-256 hash. Five wrong attempts
   against a code burn it, and `/enrol` is rate-limited per source IP.
2. At the site, someone runs `clockmanager-agent enrol --server
   https://portal.example.com.au --code K7QD-9MXT-2FWA`, or types the same
   two values into the Windows installer. The agent generates an **Ed25519
   key pair locally**. The private key never leaves the host.
3. The agent sends the code, its public key and host facts (hostname, OS,
   agent version). The server returns an agent ID in the **pending** state.
4. Both ends show the same **key fingerprint** (8 characters of base32
   SHA-256 of the public key, such as `7F3K-Q9WD`). The admin compares them
   and clicks **Approve**. A pending agent can do nothing except poll for
   approval. An unexpected pending agent is a stolen-code alarm (section 9).
5. The agent reports its devices by serial. A device already owned by
   another agent needs an admin to confirm the ownership transfer. **The
   server records exactly one owning agent per device**, which is the
   one-owner rule enforced centrally too.

### Credentials

- **Per-agent key pair, no shared secret.** To get an access token the agent
  signs a short assertion (`agent_id`, audience, issued-at, expiry of 60 s or
  less, nonce; Ed25519 over canonical JSON, so no JWT library is needed).
  The server checks it against the stored public key and returns an opaque
  1-hour access token, stored hashed. **A leak of the server database leaks
  no agent credential**, only public keys.
- **Rotation:** every 90 days, or when the heartbeat says `rotate_key`, the
  agent writes a new key as *pending*, calls `/credentials/rotate` signed by
  the old key with proof of possession of the new one, and promotes the new
  key once the server confirms. A crash in between is recovered by trying
  both keys.
- **Revocation:** **Revoke** in the portal marks the key revoked and deletes
  its access tokens, so the next request gets `401 agent_revoked`. A revoked
  agent **keeps collecting locally** (a mistaken revoke loses nothing) but
  uploads nothing and runs no commands until it is re-enrolled. Re-enrolment
  creates a new identity, and device ownership moves after an admin confirms.

### Where secrets live on the agent

The agent's secrets are its private key, its current access token and each
clock's communication password.

| Host | Storage |
| --- | --- |
| Windows service | DPAPI (`CryptProtectData` through ctypes, like `windows.py`'s mutex) under the service's virtual account `NT SERVICE\ClockManagerAgent`, in `C:\ProgramData\ClockManagerAgent`, ACL'd to that account, SYSTEM and Administrators. *To verify in 18C:* user-scope DPAPI under a virtual account survives reboots and updates. The fallback is machine-scope DPAPI plus the ACL, which is weaker because any local process can decrypt it. |
| Linux (systemd) | A dedicated system user, `StateDirectory=clockmanager-agent` (0700), key file 0600. Where available, systemd ≥ 250 `systemd-creds` / `LoadCredentialEncrypted=` bound to the TPM2. Linux has no general DPAPI equivalent for headless services: libsecret and keyrings need a login session. |
| Docker | A named volume holding the 0600 key file, container running non-root. Docker secrets are read-only and would block rotation, so they are not used. **Anyone with root on the host has the key.** This is stated, not hidden. |

The agent closes `SECURITY.md`'s open item for the device communication
password: on the agent it is encrypted at rest (DPAPI on Windows). It is
entered **locally** at the agent (`clockmanager-agent device add`, with
hidden input) and never sent to the server (D3).

---

## 4. Offline behaviour

### Local queue (agent SQLite, WAL mode)

| Table | Holds | Retention |
| --- | --- | --- |
| `devices` | Address, port, serial, encrypted communication password, local state | While configured |
| `outbox_events` | Every punch read, v2 key as primary key, `seq`, `acked_at` | **Unsent: never deleted automatically.** Acked: 90 days, then pruned. |
| `device_user_snapshots` | The last snapshot hash per device (so unchanged lists are not resent) | Latest only |
| `command_journal` | Every command received: state, result, timestamps | 1 year |
| `write_snapshots` | The pre-write 120-byte record for a remote write, encrypted like the device password. **Never uploaded.** | 30 days |
| `audit_local` | Append-only device-action audit, shipped up | 1 year after shipping |
| `sync_runs` | Pass history (the existing `sync_history` shape) | 90 days |

A punch row is about 200 bytes, so a year of unsent punches at the assumed
scale is a few MB. The heartbeat reports outbox depth and oldest unsent age.
The portal alerts when anything has been unsent for more than 1 hour, and
the agent logs a warning at 7 days.

### Replay

Oldest-first, 500 per batch, one batch in flight, backoff with jitter on
failure. A **rejected** event (for example a key mismatch or a year outside
2000–2099) is quarantined in the outbox with its reason and reported; it
never blocks the queue.

### Clock time drift

- `occurred_at_local` is the clock's own wall time, **stored verbatim and
  never corrected silently**. It is the evidence.
- On every pass the agent reads the clock's time (a proven read, PHASE 14)
  and records `offset = clock time − host time` (host time in the site's
  timezone). The host must be NTP-synced. The agent compares its own clock
  with `server_time` from the heartbeat and warns if they differ by more
  than 30 s, because a wrong host clock makes every offset wrong.
- The offset is stored per pass and attached to each batch. The portal
  alerts when |offset| exceeds **2 minutes** (default; PHASE 15 observed
  31 s) and shows it on the device page. Timesheet code may show the
  correction as a hint but never rewrites the punch.
- **Fixing the clock** is `set_time`, which is UNVERIFIED and a device
  write. It is a candidate for the gated allow-list (`device.set_time`,
  section 5.5) and is not in any early phase.
- **Daylight saving:** each site has an IANA timezone. Whether the MB1
  changes for DST by itself is **UNKNOWN**. DST starts on 2026-10-04 in
  NSW, VIC, SA, TAS and ACT. Queensland has none. Punches in the repeated
  hour at DST end are ambiguous and are flagged, not guessed.

---

## 5. Remote commands and safety

### 5.1 The envelope

```text
command_id (UUID)   tenant, site, device_serial   type (allow-listed)   params
requested_by (portal user, role)   requested_at
confirmed_by, confirmed_at, confirmation_method (typed confirmation / TOTP step-up)
expected (optimistic precondition, e.g. the target user's decoded fields)
expires_at (default requested_at + 10 min)
```

Server states: `queued → dispatched → executing → succeeded | failed |
refused | expired | outcome_unknown`. A dispatched command with no result
after 10 minutes becomes **`outcome_unknown`, never `failed`**. The portal
says so and offers a read-only refresh.

### 5.2 Every device write keeps the AGENTS.md sequence, at the agent

1. **Validate.** The server validates for the user interface. The agent
   validates with authority: the command type is on the local allow-list;
   the gate is on; this agent owns the device; the command has not expired;
   the `command_id` is not in the journal; the parameters pass
   `UserDraft.validate` and the builder budgets (9-byte user ID, 23-byte
   last name, the PHASE-15 incident limits); and `expected` matches the
   record read now. A stale edit is refused, not applied.
2. **Confirm.** The portal requires an explicit confirmation from the
   requester: TOTP step-up for any write, and typed confirmation of the user
   ID for a delete. The envelope carries who confirmed and how. The agent
   refuses a write envelope without them. The agent **cannot prove** the
   confirmation happened (a compromised server could fake it), which is
   exactly why the gates live at the agent (5.3).
3. **Execute.** Through the device owner (5.4): pause live capture, take the
   single session, do a health read, snapshot the target 120-byte record
   into `write_snapshots`, then execute through the **existing**
   `services.users` write path. No new write path is written for remote use.
4. **Read back.** Compare decoded fields, never bytes (PROTOCOL.md, "Read-back
   verification compares meaning, not bytes"). The result carries the
   decoded fields (never the credential region) and `verified: true/false`.
5. **Audit.** Every outcome, including refusals and gate-closed refusals, is
   written to `audit_local` before the result is sent, then shipped. The
   server audit row carries the same `command_id`.

### 5.3 The gates are enforced at the agent, and the server cannot override them

- `enable_device_writes` and `enable_credential_writes` are read **only**
  from the agent's local config file or environment
  (`CLOCKMANAGER_ENABLE_DEVICE_WRITES`, ...), exactly as today.
- New local-only keys: `allowed_remote_writes` (a list of command types,
  **empty by default**), `max_remote_writes_per_hour` (default 10) and
  `min_healthy_passes_before_write` (default 3).
- **The remote-config schema has no field** for any of these, and a test
  pins that. The config loader also ignores them from any remote source.
  The heartbeat reports them so the portal can grey out buttons, but that
  is display only.
- **Credential writes are not a remote command at all.** No `user.set_pin`
  type exists, so `enable_credential_writes` only ever applies to local
  tools. A PIN typed into the portal would reach the site through the
  server, which this design does not allow (D2).

### 5.4 One command at a time per device

- 18A introduces a **per-device owner** in the agent: one worker per device
  holds the only session. Periodic passes, live capture, reads and commands
  all queue through it, so live capture is paused, never run alongside.
- The agent pulls the next command only after posting the previous result.
- The server enforces it too: a partial unique index allows only one
  `dispatched`/`executing` command per device.

### 5.5 The allow-list

| Command | Kind | Phase | Gate |
| --- | --- | --- | --- |
| `device.sync_now` | read | 18E | none |
| `device.users_refresh` | read | 18E | none |
| `device.inspect` (info, capacity, allow-listed options) | read | 18E | none |
| `device.test_connection` | read | 18E | none |
| `device.resync_full` (re-upload the whole log) | read | 18E | none |
| `user.create` | write | 18F | writes + listed in `allowed_remote_writes` |
| `user.update_name` | write | 18F | same |
| `user.set_privilege` | write | 18F | same |
| `user.delete` | write, destructive | 18F | same, and typed confirmation |
| `device.set_time` | write | later, after proof | same (UNVERIFIED on MB1) |

**Not expressible, ever:** PIN, card or any credential write; any biometric
operation; factory reset; clear attendance; option writes
(`WRITE_DEVICE_OPTIONS` is UNSUPPORTED); raw protocol commands; shell
commands; anything that changes agent configuration. `CMD_RESTART` is not
on the list (D7): it only works while the protocol is healthy, and
rebooting the only clock mid-shift is its own outage. LAN discovery stays a
local CLI command (`clockmanager-agent discover`) in the early phases.

### 5.6 When the clock stops answering

The PHASE 15 incident sets the rules: TCP 4370 stays open while the device
is dead, and there is no remote reset.

- **Health is a completed protocol read, not an open port.** A write needs
  `min_healthy_passes_before_write` consecutive healthy passes first.
- If a write times out, or any command is followed by failed sessions, the
  agent **quarantines** the device. Writes are refused automatically, reads
  continue, and the agent falls back to the patient reconnect loop (the
  existing 30 s doubling backoff, capped at 10 min). It reports
  `device_unresponsive`, and the portal raises an alert.
- Quarantine is a safety interlock, not a gate. An admin may clear it in
  the portal, but the agent accepts the clear only after the device has
  been healthy again for the configured number of passes.
- The agent **never** sends `CMD_RESTART` by itself.

### 5.7 An agent crash in the middle of a command

The journal records `executing` before the device is touched. On restart,
an `executing` entry is **never re-executed**. The agent does a read-only
read-back of the target, reports `outcome_unknown` with what it found, and
a person decides. Writes are never retried automatically.

---

## 6. Central data model (Postgres 16+)

Every table has `tenant_id NOT NULL`. Today there is one tenant row. Every
query is scoped by tenant in the repository layer, with Postgres row-level
security as a later defence in depth (18G).

| Table | Key columns | Notes |
| --- | --- | --- |
| `tenants` | id, name, status | One row for now |
| `sites` | id, tenant_id, name, **timezone (IANA)** | |
| `agents` | id, tenant_id, site_id, public_key, key_fingerprint, status (pending/active/revoked), version, os, last_seen_at, last_heartbeat jsonb | |
| `pairing_codes` | code_hash, tenant_id, site_id, expires_at, used_at, attempts, created_by | |
| `agent_tokens` | token_hash, agent_id, expires_at | |
| `devices` | id, tenant_id, site_id, **serial_number** (unique per tenant), owning_agent_id, name, model, platform, firmware, capabilities jsonb, has_communication_password, quarantined_at, last_seen_at | No address and no secret: the site LAN is the agent's business |
| `device_users` | tenant_id, device_id, device_uid, user_id, display_name, privilege, has_credential_data, finger_count, snapshot_at | **No PIN, card or template column exists** |
| `attendance_events` | id, tenant_id, device_id, event_key, device_uid, user_id, **occurred_at_local `timestamp without time zone`**, punch, status, source, captured_at, received_at, agent_id, batch_id, employee_name, device_clock_offset_s | UNIQUE (tenant_id, device_id, event_key) and the natural key |
| `sync_runs` | per device, per pass, reported by agents | The existing `sync_history` shape |
| `employees`, `employee_device_links`, `pay_schedules` | as today plus tenant_id | |
| `app_users` | as today plus tenant_id, totp_secret (encrypted), failed_attempts, locked_until | |
| `sessions` | token_hash, user_id, created_at, expires_at, last_seen_at, ip | Replaces in-memory tokens |
| `commands` | section 5.1 | Partial unique index: one in flight per device |
| `audit_events` | tenant_id, actor_type (user/agent/system), actor_id, action, outcome, device_id, command_id, origin (server/agent/desktop-import), occurred_at, received_at, detail (redacted) | Append-only: the app role has no UPDATE or DELETE grant |

**Why `timestamp without time zone`:** today `occurred_at` is naive
device-local time in a `DateTime(timezone=True)` column. SQLite ignores the
difference. **Postgres would read the naive value as the session timezone
and shift every punch** (R3). Device wall time plus the site's IANA zone is
the honest representation.

**Migrations:** Alembic for the Postgres schema (the one new server
dependency justified by production data, D10). The desktop's in-house
SQLite migrations are untouched.

### Migrating existing SQLite desktop installs (18D)

1. **Stop the desktop collecting** (tray *Quit*, remove from startup).
   One owner at all times.
2. Take a PHASE 09 backup zip.
3. Install and enrol the agent. Add the clock locally (the communication
   password is typed at the agent). The agent reports it by serial, and the
   admin approves.
4. The agent does a full read and uploads it.
5. On the server, `clockmanager-server import-sqlite --backup <zip> --site
   <site>`:
   - devices are matched by `serial_number`. An imported device with no
     serial must be mapped by hand to an agent-reported serial before
     anything imports;
   - attendance is **re-keyed to v2** from that serial, so overlap with
     step 4 dedupes to nothing;
   - employees, links, pay schedules and portal accounts are imported.
     Password hashes import as they are (same verifier), and TOTP enrolment
     is forced at first sign-in;
   - audit rows are imported with `origin=desktop-import` and their
     original times;
   - **the communication password column is skipped.** It never reaches
     the server.
6. Reconcile: per device, server count ≥ desktop count, and every desktop
   key is present.
7. The desktop app switches to its post-agent role (section 8).

---

## 7. Packaging

One codebase with a new `clockmanager-agent` entry point (`enrol`, `run`,
`status`, `device add|list|remove`, `discover`, `pause`, `resume`). The
agent never imports PySide6, and the existing PySide6-free core already
makes that true.

### Windows service

- A separate **per-machine** installer, `ClockManagerAgent-Setup-x.y.z.exe`
  (Inno Setup, admin), installs to `C:\Program Files\ClockManager Agent` and
  keeps data in `C:\ProgramData\ClockManagerAgent`. It prompts for the
  server URL and pairing code, or takes `/SERVER=` and `/CODE=` for silent
  installs. The existing per-user desktop installer is unchanged.
- The service runs the PyInstaller console build of `clockmanager-agent run`
  under **WinSW** (a single MIT-licensed wrapper exe; recommended over
  pywin32's in-process service code, D10). It uses the virtual account
  `NT SERVICE\ClockManagerAgent`, starts *Automatic (Delayed)*, and restarts
  on failure.
- **This is what removes v0.14.0's "needs a login" limit.** The service
  starts with Windows and collects with nobody signed in. The tray app is
  no longer the collector (section 8). It can show agent health read from
  the agent's `127.0.0.1` health endpoint.
- The agent takes a machine-wide mutex per device
  (`Global\ClockManager-Device-<serial>`) that the desktop app checks before
  connecting (18A). That covers the same-host case. Different hosts are
  covered by the desktop's "managed by site agent" flag and the portal's
  single owner.

### Linux

- **Docker:** a `clockmanager-agent` image from the PHASE 16 Dockerfile
  (non-root, `/data` volume, `HEALTHCHECK` against the localhost health
  endpoint). **No published ports.** Outbound TCP to 4370 works from the
  default bridge network. A Synology Container Manager compose file is
  included.
- **systemd:** a unit file shipped with a pipx/wheel install:
  `Restart=always`, a dedicated user, `StateDirectory=`,
  `NoNewPrivileges=yes`, `ProtectSystem=strict`, `PrivateTmp=yes`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, and optional
  `LoadCredentialEncrypted=`.

### The server (own VPS)

A `docker-compose.yml` with a reverse proxy that handles automatic TLS
(Caddy), the app (uvicorn), Postgres 16, and a nightly `pg_dump` encrypted
and copied **off the VPS**. Postgres is not exposed, and only 80 and 443
are open. The operator is responsible for OS patching and for testing
restores (R8).

### Auto-update

- The agent learns that a version exists from the heartbeat. **The server
  only names a version.** The agent downloads from its own configured
  release source (the existing tag-driven GitHub Releases) and **verifies a
  signature made by a release key that is not on the server**
  (minisign/Sigstore; Authenticode as well on Windows, D4). A compromised
  server therefore cannot push code. There is no downgrade below the
  installed version.
- **Windows:** on by default. It installs only inside a maintenance window
  (default 22:00–04:00 site time), never with a command in flight, and
  rolls back if the new version is not healthy within 5 minutes. With more
  than one site, one site updates first.
- **Docker and systemd:** notify only. The portal shows "update available"
  and the operator runs `docker compose pull` or upgrades the package.
  Automatic image updates (Watchtower-style) are not recommended for
  something that owns a clock.

---

## 8. The desktop app under the one-owner rule

| Option | Verdict |
| --- | --- |
| Client of the agent (a local API) | **No.** It would duplicate the portal's UI against a second, local API that has to be secured and versioned, for one screen's worth of benefit. |
| Client of the portal | **No, not as a product.** The browser does this better, and PHASE 19 builds it. |
| **Retired as a collector, kept briefly as a break-glass tool** | **Recommended (D1).** |

- When a site gets an agent, each of its devices is marked **"managed by
  site agent"** in the desktop database (18A). The desktop then refuses
  sync, live capture and writes for that device and says why. Its history
  stays readable.
- **Break-glass:** for an internet outage that lasts days, or for PIN
  enrolment work, a technician on the agent host runs
  `clockmanager-agent pause --device <serial> --minutes 30`. The agent
  releases the device and its mutex, and the desktop app can own it until
  the pause ends. Every pause is audited, and the agent resumes by itself.
- The desktop app is kept for **one release cycle after 18D**. After that
  it is either kept as the break-glass tool or retired, decided on how
  often break-glass was actually needed.

---

## 9. Threat model

### What never leaves the site (structural, not policy)

| Never leaves the site | How it is enforced |
| --- | --- |
| User PINs (credential region, bytes 3:11) | `RawUserRecord.raw` stays in `protocol`; the upload schemas have no field for it; `write_snapshots` is never uploaded |
| Full card numbers | Not read (UNVERIFIED offset); no schema field |
| Biometric templates | Discarded inside `parse_fingerprint_payload`; only the count is uploaded |
| The raw 120-byte record, and `CMD_DB_RRQ` payloads | Confined to `protocol`; withheld from traces |
| Device communication password | Local entry, encrypted at rest; no schema field |
| Protocol traces and raw packets | Diagnostics stay on the agent; a remote "diagnostics summary" (later) is redacted counts only |
| Agent private key | Generated and kept on the host |

**Goes to the server:** display names, user IDs, device UIDs, privilege,
`has_credential_data`, finger count, punches (time, punch, status),
employees and pay settings, device inventory and health. All of this is PII
or payroll data and is protected accordingly: TLS in transit, an Australian
VPS, 7-year retention, encrypted off-site backups, and redacted logs.

### Threats

| Threat | What the attacker gets | Mitigations | Residual |
| --- | --- | --- | --- |
| **Compromised agent** (malware or a stolen PC) | The agent key; the site LAN, which already gives direct 4370 access; the ability to upload forged punches for **its own** devices | The agent API is ingest-only: no endpoint returns employee data. Punches for devices it does not own are rejected. Anomaly flags (future-dated, older than the clock log, volume spikes). Every row carries `agent_id` and `batch_id`, so a bad agent's rows can be isolated. **The clock log is ground truth:** `device.resync_full` from a clean agent flags server rows the device never held. Revoke. | LAN access to the clock is the same as today. A forged punch within normal bounds needs the reconciliation to catch it. |
| **Compromised server** | All central PII and payroll data; the ability to queue allow-listed commands; fake confirmations | Gates, `allowed_remote_writes`, rate limit and quarantine live **at the agent**. No credential or biometric command exists. Updates need a signature the server cannot make. Only agent public keys are stored. No PIN, card, template or device password is on the server. | With writes enabled at a site, an attacker can create, rename or delete device users (people cannot clock in) at 10 per hour. Local snapshots allow a manual restore. Keep `allowed_remote_writes` minimal (no `user.delete` unless needed). |
| **Stolen pairing code** | A race to enrol a rogue agent | 15-minute TTL, single use, bound to a site, admin approval with fingerprint comparison; a pending agent can do nothing; a claim on an owned device needs a transfer confirmation | A careless admin approving the wrong fingerprint. The portal highlights "a second agent for this site". |
| **Stolen portal password** | A portal session | TOTP, lockout and throttling (today's API has neither), server-side sessions that can be revoked, TOTP step-up for device writes | A phished TOTP within its 30-second window |
| **Network attacker between agent and server** | Traffic | TLS with OS trust; access tokens valid for 1 hour; idempotent replays | A TLS-inspecting proxy at a site sees the traffic (by design of that site) |
| **Attacker on the site LAN** | The ZK protocol and its weak communication key | Out of scope, as today. The agent adds no inbound port. | Unchanged |

---

## 10. Alternative worth checking: ZKTeco "Cloud Server Setting" (ADMS / iclock push)

> **UNVERIFIED for the NG-MB1 (ZMM510_TFT, Ver 8.0.4.5-7108-02).**
> Everything below comes from public sources about the ZKTeco family. The
> device was not probed, and it must not be probed to find out.

### What it is (public sources)

Many ZKTeco-family devices can be pointed at a server ("Cloud Server
Setting", also called ADMS or PUSH). The device then makes HTTP requests
itself:

- a handshake, `GET /iclock/cdata?SN=<serial>&options=all`. The server
  replies with options such as `ATTLOGStamp` (the last record it has, which
  is how backfill works), `Delay`, `ErrorDelay`, `TransTimes`,
  `TransInterval`, `TransFlag` (which tables to push, including
  fingerprint, face and user-photo tables) and `Realtime`;
- punches arrive as `POST /iclock/cdata?SN=…&table=ATTLOG`, with
  tab-separated lines (user ID, time, status, verify mode, work code). The
  server must answer `OK`;
- the device polls `GET /iclock/getrequest?SN=…` for server commands such as
  `C:<id>:DATA UPDATE USERINFO …` and reports results to
  `POST /iclock/devicecmd`.

Public implementations report plain HTTP on most firmware (HTTPS only on
some newer models), **device identity is the serial number alone** (printed
on the label, with no cryptographic authentication), and some firmware
rejects documented commands (for example `-1002` for `USER ADD`). NGTeco
sells its own cloud clocks (TC1, TC2, TC4) as a separate product line,
which suggests but does not prove that standalone models like the MB1 may
not carry push firmware.

### Compared with the agent

| | Site agent (this design) | ADMS push |
| --- | --- | --- |
| Software at the site | Yes, an agent on a PC, NAS or small Linux box | None |
| Proven on this MB1 | Read path proven (PHASE 14), write path proven (PHASE 15) | **Unknown whether the menu even exists** |
| Transport security | TLS, per-agent keys | Often plain HTTP; identity is a serial anyone can read off the label |
| Server exposure | The server accepts HTTPS from authenticated agents | The server must accept unauthenticated HTTP from the internet for any claimed serial |
| Write gates | At the site; the server cannot override them | **None at the site.** The server can write users directly, which breaks this project's gate rule |
| Biometric data | Never leaves the site | `TransFlag` can push templates and photos; whether the firmware honours "off" is unverifiable |
| Backfill | Full log re-read plus v2 keys | `ATTLOGStamp`, decided by the device |
| One owner | The agent is the only session | An extra data channel. How it interacts with 4370 sessions is unknown. |
| Vendor documentation | Our own adapter and fixtures | Unofficial PDFs and reverse-engineered libraries |

**Verdict:** the agent stays the design. ADMS is only worth a future
**read-only, punches-only** ingest endpoint (18H, optional) for a tiny site
with no always-on computer, and only if the operator check below finds the
menu **and** it supports HTTPS. Even then the endpoint would never issue a
command, and `TransFlag` would exclude every biometric table.

**Operator action:** at the clock, open **Menu > Comm** (on ZK firmware
usually *COMM. > Cloud Server Setting*, sometimes *ADMS*). **Look only;
change nothing.** Photograph the screen. Record whether the option exists
and whether it offers HTTPS, a domain name or a proxy.

---

## 11. Roadmap

Each step ships something usable. Effort is in Claude-assisted working
sessions of about a day, the unit most PHASE entries in `CHANGELOG.md`
took.

| Step | Ships | Usable result | Effort |
| --- | --- | --- | --- |
| **18** (this brief) | The design | Decisions can be made | done on review |
| **18A** Core prerequisites | Per-device session owner (no concurrent live and sync sessions); machine-wide per-device lock shared by the GUI, `--serve` and `--api-serve`; the desktop's "managed by site agent" flag; v2 event key next to v1; DPAPI for the stored device password | Today's `--serve` and desktop app obey the one-owner rule, and the `SECURITY.md` open item is closed on Windows | 2–3 |
| **18B** Portal server and ingest | Postgres and Alembic schema (section 6); enrolment, approval, tokens, heartbeat, punches, devices and device-user endpoints; local accounts with TOTP, lockout and server-side sessions; minimal read-only pages (sign in, punches, agents and devices); VPS compose with TLS and encrypted off-site backups | A portal you can sign in to, tested against a fake agent | 6–9 |
| **18C** Agent MVP | `clockmanager-agent` (enrol, run, status, device add, pause); outbox, uploader, heartbeat, snapshots; Windows service with per-machine installer, Docker image and systemd unit | **MVP = 18A + 18B + 18C: punches flow from the site to the portal and are visible there. Read-only.** | 6–9 |
| **18D** Migration and cut-over | `import-sqlite`, v2 re-keying, device mapping, reconciliation report, cut-over runbook | The first real site cut over, with its full history in the portal | 3–4 |
| **19** Web frontend (`PHASE-19.md`) | The full portal UI: employees, timesheets, reports, audit, device status, live view | The desktop app's screens are available in the browser | 8–12 |
| **18E** Read-only commands | Long-poll channel, command journal, the five read commands, portal buttons | "Sync now", "Refresh users" and "Resync" from anywhere | 3–5 |
| **18F** Gated remote writes | `user.create`, `update_name`, `set_privilege`, `delete` through the full sequence; quarantine; TOTP step-up; hardware verification on a `ZZTEST-` account **through the agent** | Remote user management where a site opts in | 5–7 |
| **18G** Operations | Signed auto-update (Windows) and update notices (Docker/Linux), key rotation, alerts (agent offline, clock unresponsive, drift, outbox age), row-level security | Runs unattended across more than one site | 4–6 |
| **18H** *(optional)* ADMS spike | A read-only iclock punches endpoint | Only if the operator check passes | 2–3 |

**Order:** 18A → 18B and 18C (can overlap once the schemas are fixed) →
18D. Then 19 and 18E can run in parallel. 18F comes after 18E. 18G comes
after the MVP and before a second site. **To the MVP: about 14–21 sessions.
Through 18G, including 19: about 37–55.**

---

## 12. Open questions and risks

### Decisions for the operator (D)

- **D1 — Desktop app future.** Recommended: retire it as a collector and
  keep it for one release cycle as a break-glass tool (section 8).
- **D2 — PIN management.** Recommended: PINs are set only at the clock's
  keypad or through a local tool during a break-glass pause. They are never
  entered in the portal. (The alternative is encrypting to the agent's key
  in the browser, but a compromised server could substitute that key.)
- **D3 — Device communication password.** Recommended: entered only at the
  agent. The server never holds it.
- **D4 — Auto-update and signing.** Automatic on Windows inside a window
  (recommended), or notify-only everywhere. Buy an Authenticode certificate
  (a yearly cost; it also stops SmartScreen warnings), or rely on
  minisign/Sigstore alone.
- **D5 — VPS provider, region, domain and backup target.** An Australian
  region is recommended. The portal hostname, and where encrypted backups
  go, are also needed.
- **D6 — Approval step after pairing.** Recommended: yes (section 3).
- **D7 — `CMD_RESTART` as a remote command.** Recommended: no, for now.
- **D8 — Retention.** 7 years on the server (confirm with the accountant)
  and 90 days of acked punches on the agent.
- **D9 — Drift threshold, and `device.set_time`.** Recommended: a 2-minute
  alert. `set_time` stays off until it is proven on a device with a real
  reason to change the clock.
- **D10 — New dependencies:** `cryptography` (agent and server), Alembic and
  psycopg (server), WinSW (a bundled exe).
- **D11 — Alert channel** for "agent offline" or "clock unresponsive": email
  through the business's mail system, or something else.
- **D12 — Remote writes at your own site.** Whether 18F is wanted at all,
  and if it is, which commands go in `allowed_remote_writes`. Recommended:
  create and rename only, no delete.

### Risks (R)

- **R1 — The one-owner rule is broken in today's code** (section 1: `--serve
  --live`, the GUI-only lock, device I/O in `--api-serve`). HIGH. Fixed in
  18A, before any agent.
- **R2 — v1 event keys are not portable** (local `device_id`). HIGH for
  migration. v2 keys in 18A.
- **R3 — Naive device time in `timestamptz` shifts on Postgres.** HIGH if
  missed. `timestamp without time zone` plus the site timezone.
- **R4 — Whole-log re-read on every pass.** At the 30,000-record cap that is
  about 1.2 MB per pass, and the default pass is every 60 s, against a
  device that copes badly with load. Measure in 18C. With working live
  capture, a 5-minute pass is probably enough.
- **R5 — Live capture depends on pyzk private attributes** (pinned `==0.9`)
  and on a live-event layout that is still UNDETERMINED.
- **R6 — What the MB1 does when its log is full is UNKNOWN.** It may stop
  recording or overwrite. The portal should warn at 80% of `rec_cap`, and
  the answer needs vendor documentation, not an experiment.
- **R7 — Remote writes make device writes easier.** The clock has no remote
  reset. The gates, quarantine, rate limit and the one-at-a-time rule are
  the mitigation. PHASE 15's lessons apply unchanged.
- **R8 — Own-VPS operations.** Patching, TLS, backups and monitoring belong
  to the operator, and the VPS is a single point of failure. Agents buffer,
  so a VPS outage loses no punches, but the portal is down until it is
  back.
- **R9 — Windows signing.** Without a certificate, SmartScreen warns and
  auto-update depends on minisign/Sigstore only.
- **R10 — DST behaviour of the MB1 is UNKNOWN** (see the operator actions).
- **R11 — ADMS support on this firmware is UNKNOWN** (section 10).
- **R12 — Keypad-only PINs** are less convenient (D2).
- **R13 — TLS-inspecting proxies** at a site rule out certificate pinning.
  The agent must honour proxy settings.
- **R14 — Late punches after an export** need pay-period locking, which
  does not exist yet (section 2.4).
- **R15 — DPAPI under a virtual service account is untested here.** Verify
  in 18C; the fallback is weaker (section 3).

### Operator actions (physical; no software involved)

1. At the clock: **Menu > Comm**. Look for *Cloud Server Setting* / ADMS,
   photograph it, change nothing (section 10).
2. On or after **2026-10-04**, if the site observes DST, compare the clock's
   displayed time with a phone. Did it change by itself?
3. Confirm which computer will host the first agent. It must be always on,
   NTP-synced and on the clock's LAN.
4. Still outstanding from PHASE 15: re-enrol both fingerprints, and delete
   `ZZTEST-LONGID` (UID 901) from the keypad.

---

## Sources (section 10, public only)

- [s0x90/zkteco-adms (Go ADMS library): endpoints, command format, firmware caveats](https://github.com/s0x90/zkteco-adms)
- [Attendance PUSH Communication Protocol 20200325 (Scribd)](https://www.scribd.com/document/604032067/Attendance-PUSH-Communication-Protocol-20200325)
- [shashinvision/iclock `cdata.php` (TransFlag tables)](https://github.com/shashinvision/iclock/blob/main/cdata.php)
- [Upeosoft-Limited/zkteco_http_listener](https://github.com/Upeosoft-Limited/zkteco_http_listener)
- [fedotovaleksandr/iclockhelper](https://github.com/fedotovaleksandr/iclockhelper)
- [Integrating biometric attendance machines with your own server (techresolve.blog)](https://techresolve.blog/2026/03/09/how-to-integrate-biometric-attendance-machines-wit/)
- [NGTeco cloud time clocks (vendor product pages)](https://ngteco.com/pages/cloud)
