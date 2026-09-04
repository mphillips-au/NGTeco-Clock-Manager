"""Sync key and reconciliation unit tests (PHASE 04)."""

from __future__ import annotations

from datetime import UTC, datetime

from clockmanager.domain.models import AttendanceEvent
from clockmanager.sync.engine import plan_inserts
from clockmanager.sync.keys import build_event_key, normalise_for_key
from clockmanager.sync.sources import SyncSource

MOMENT = datetime(2026, 3, 2, 8, 0, 0)  # noqa: DTZ001


def _event(user_id: str = "1001", **overrides: object) -> AttendanceEvent:
    base: dict[str, object] = {
        "user_id": user_id,
        "occurred_at": MOMENT,
        "punch": 0,
        "status": 0,
        "device_uid": 1,
    }
    base.update(overrides)
    return AttendanceEvent(**base)  # type: ignore[arg-type]


class TestEventKeys:
    def test_same_punch_produces_the_same_key(self) -> None:
        first = build_event_key(device_id=1, user_id="1001", occurred_at=MOMENT, punch=0, status=0)
        second = build_event_key(device_id=1, user_id="1001", occurred_at=MOMENT, punch=0, status=0)
        assert first == second
        assert len(first) == 64  # SHA-256 hex

    def test_key_covers_every_natural_key_field(self) -> None:
        base = {"device_id": 1, "user_id": "1001", "occurred_at": MOMENT, "punch": 0, "status": 0}
        key = build_event_key(**base)  # type: ignore[arg-type]
        assert build_event_key(**{**base, "punch": 1}) != key  # type: ignore[arg-type]
        assert build_event_key(**{**base, "status": 3}) != key  # type: ignore[arg-type]
        assert build_event_key(**{**base, "user_id": "1002"}) != key  # type: ignore[arg-type]
        assert build_event_key(**{**base, "device_id": 2}) != key  # type: ignore[arg-type]
        other_time = datetime(2026, 3, 2, 8, 0, 1)  # noqa: DTZ001
        assert build_event_key(**{**base, "occurred_at": other_time}) != key  # type: ignore[arg-type]

    def test_naive_and_utc_aware_wall_time_share_a_key(self) -> None:
        """SQLite returns naive datetimes; the key must survive the round trip."""
        naive = datetime(2026, 3, 2, 8, 0, 0)  # noqa: DTZ001
        aware = datetime(2026, 3, 2, 8, 0, 0, tzinfo=UTC)
        assert normalise_for_key(naive) == normalise_for_key(aware)

    def test_microseconds_are_ignored(self) -> None:
        plain = datetime(2026, 3, 2, 8, 0, 0)  # noqa: DTZ001
        micro = datetime(2026, 3, 2, 8, 0, 0, 123456)  # noqa: DTZ001
        assert normalise_for_key(plain) == normalise_for_key(micro)


class TestPlanInserts:
    def test_empty_input_plans_nothing(self) -> None:
        plan = plan_inserts(device_id=1, events=[], known_keys=set())
        assert plan.to_insert == ()
        assert plan.counts.seen == 0
        assert plan.counts.new == 0

    def test_new_events_are_planned_with_employee_snapshot(self) -> None:
        plan = plan_inserts(
            device_id=1,
            events=[_event("1001"), _event("9999")],
            known_keys=set(),
            users_by_id={"1001": "Ada Lovelace"},
        )
        assert plan.counts == plan.counts  # dataclass equality sanity
        assert plan.counts.seen == 2
        assert plan.counts.new == 2
        assert plan.counts.duplicate == 0
        assert plan.to_insert[0].employee_name == "Ada Lovelace"
        # Unknown UID is stored with no snapshot, never dropped.
        assert plan.to_insert[1].employee_name is None

    def test_known_keys_are_skipped(self) -> None:
        key = build_event_key(device_id=1, user_id="1001", occurred_at=MOMENT, punch=0, status=0)
        plan = plan_inserts(device_id=1, events=[_event("1001")], known_keys={key})
        assert plan.counts.new == 0
        assert plan.counts.duplicate == 1
        assert plan.to_insert == ()

    def test_duplicates_within_one_batch_count_once(self) -> None:
        plan = plan_inserts(device_id=1, events=[_event("1001"), _event("1001")], known_keys=set())
        assert plan.counts.new == 1
        assert plan.counts.duplicate == 1

    def test_natural_key_fallback_catches_pre_key_rows(self) -> None:
        """Rows stored before event keys existed still suppress re-inserts."""
        natural = {("1001", MOMENT.replace(microsecond=0), 0, 0)}
        plan = plan_inserts(
            device_id=1, events=[_event("1001")], known_keys=set(), known_natural_keys=natural
        )
        assert plan.counts.new == 0
        assert plan.counts.duplicate == 1

    def test_status_is_part_of_identity_but_never_interpreted(self) -> None:
        first = _event("1001", status=0)
        second = _event("1001", status=5)
        plan = plan_inserts(device_id=1, events=[first, second], known_keys=set())
        assert plan.counts.new == 2  # same punch time, different raw status: two rows

    def test_sync_source_values_are_stable(self) -> None:
        assert SyncSource.HISTORICAL.value == "historical"
        assert SyncSource.MANUAL.value == "manual"
        assert SyncSource.LIVE.value == "live"
        assert SyncSource.BACKGROUND.value == "background"
        assert SyncSource.RECOVERY.value == "recovery"
