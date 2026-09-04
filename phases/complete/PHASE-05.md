# PHASE 05 — Employees / Timesheets / Payroll

## Goal
Build the business layer above raw attendance.

## Read
- AGENTS.md
- PLAN.md
- STATUS.md
- CHANGELOG.md
- ARCHITECTURE.md
- TESTING.md

## Employee
internal ID, device mapping, user ID, names, active, department, position, email, notes.

## Pay periods
Weekly, Bi-weekly, Semi-monthly, Monthly.
Support period start, cutoff, duplicate interval, maximum shift, HH:MM/decimal.

## Calculate
daily duration, first IN, last OUT, missing punches, duplicate punches, excessive shifts, overnight shifts, daily/weekly/pay-period totals, overtime if configured.

Raw attendance remains immutable.
Timesheets are derived and recalculable.

Test boundaries and timezone/DST behavior.
