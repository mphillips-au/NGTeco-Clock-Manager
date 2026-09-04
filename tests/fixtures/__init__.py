"""Sanitized MB1 protocol fixtures.

``AGENTS.md`` and ``SECURITY.md`` forbid committing real credentials or raw
credential-containing captures. Everything here is synthetic: names, user IDs
and the credential region are constructed in code, and the credential region is
filled with an obvious non-secret marker byte rather than any real PIN.
"""

from __future__ import annotations

from tests.fixtures.mb1 import (
    ADMIN_PRIVILEGE,
    EMPLOYEE_PRIVILEGE,
    build_attendance_payload,
    build_attendance_record,
    build_live_event,
    build_user_payload,
    build_user_record,
    encode_zk_time,
    sample_users,
)

__all__ = [
    "ADMIN_PRIVILEGE",
    "EMPLOYEE_PRIVILEGE",
    "build_attendance_payload",
    "build_attendance_record",
    "build_live_event",
    "build_user_payload",
    "build_user_record",
    "encode_zk_time",
    "sample_users",
]
