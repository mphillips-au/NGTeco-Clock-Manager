# Build Prompt Index

Universal rules are now in `AGENTS.md`.

For every coding session:
1. Paste/use `AGENT_SESSION_TEMPLATE.md`.
2. Give the agent the current `phases/PHASE-XX.md`.
3. Let the agent read `AGENTS.md`, `PLAN.md`, `STATUS.md`, `CHANGELOG.md`.
4. Only bring in additional docs when the phase needs them.
5. Run one phase per session.

Phase order:
- 00 Repository Bootstrap
- 01 MB1 Protocol Core
- 02 Windows GUI / Settings / Diagnostics
- 03 User Management
- 04 Attendance Synchronization
- 05 Employees / Timesheets / Payroll
- 06 Reports / Exports
- 07 Authentication / Roles / Audit
- 08 Device Management / Discovery
- 09 Backup / Offline Resilience
- 10 Developer Diagnostics
- 11 Biometric / Card Investigation
- 12 UI Polish
- 13 Windows Packaging
- 14 Production QA
- 15 Synology/Linux Service
- 16 Web API
- 17 Web Frontend

The detailed requirements live in the corresponding phase file.
