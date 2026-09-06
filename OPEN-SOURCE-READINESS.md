# Open-source readiness checklist

Assessment written 2026-09-06. The code itself is already public-safe; the
publishing files are not. This checklist tracks what must be fixed before
(or at) going public. Remaining product work (PHASE 18, hardening) is out
of scope here and stays tracked in `STATUS.md` / `phases/PHASE-18.md`.

## Already verified clean (2026-09-06)

- Working tree clean, no open PRs; history is linear through PR #15.
- `local/` (databases, logs, config) is git-ignored; no `.db`, `.log`,
  `.pcap`, or `secrets/` files are tracked.
- No real secrets in tracked code: the `password=...` hits are test
  fixtures (`"1234"`, `"new-password-1"`); the `192.168.x` hits are example
  placeholders or documented device behavior.
- No `requirements.txt` / `docs/` directory exists (both referenced by the
  README — see below).

## Blockers — fix before publicising

- [ ] **Add a `LICENSE` file.** `README.md` promises MIT (`See LICENSE`,
  no file exists) while `pyproject.toml` declares
  `license = { text = "Proprietary" }`. Pick one, add the file, fix the
  field. Note the history is already pushed to
  `mphillips-au/NGTeco-Clock-Manager`, so decide before wider cloning.
- [ ] **Rename throughout: ClockBridge → NGTeco Clock Manager.**
  44 occurrences in `README.md` (title, body, ASCII diagrams, install
  commands, bug-report template's `ClockBridge version:` field) plus one
  parenthetical in `PACKAGING.md:3`. No code references the old name
  (package is `clockmanager`, distribution `ngteco-clock-manager`).
- [ ] **Rewrite the README's stale sections:**
  - Install instructions: `pip install -r requirements.txt` (no such
    file) → `pip install -e .[gui,dev]`; `python -m clockbridge` →
    `clockmanager`; clone URL placeholder
    `YOUR_USERNAME/clockbridge` → the real remote.
  - `docs/images/clockbridge-dashboard.png` is referenced but `docs/`
    does not exist — add a real screenshot or drop the image.
  - "Current focus" checklist marks user management, sync, employees,
    timesheets, reports, auth, backup, packaging, Linux service and REST
    API as `[ ]` — all shipped (PHASEs 03–09, 13, 16, 17). Update to
    match `STATUS.md`.
  - "Future: Linux & NAS" and "Future API & Web UI" describe PHASE 16/17
    as planned; both are complete. Rewrite as shipped, with the
    `--serve` / `--api-serve` entry points.
  - Mentions Alembic; the project uses forward-only hand migrations.
- [ ] **`pyproject.toml` metadata:** fix the license field (above) and add
  `urls` (Homepage/Repository). Authors field is a generic placeholder.
- [ ] **Decide on personal/hardware identifiers in tracked docs:** real
  names (`AGENTS.md`, `CHANGELOG.md`), device serial `NBF6260700048`,
  LAN IPs. IPs are RFC-1918 (low risk); the serial/firmware is useful
  hardware provenance. Scrubbing now requires a history rewrite +
  force-push since it is already on GitHub. Suggested: keep the serial,
  pseudonymise the employee surname going forward, and consider moving
  the AGENTS.md test-target authorisation note to a private ops note.
- [ ] **GitHub repo settings:** About description, topics, and confirm the
  repo name matches the distribution name (`ngteco-clock-manager` vs
  `NGTeco-Clock-Manager`).

## Deliberately not in this checklist

- PHASE 18 web frontend, operator physical actions (fingerprint
  re-enrolment, keypad delete of UID 901), security hardening (DPAPI,
  login throttling), and protocol gaps — all tracked in `STATUS.md`.
