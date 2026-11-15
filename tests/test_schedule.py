from datetime import datetime, timezone

from scanner.schedule import plan


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_early_trigger_waits_for_the_slot():
    assert plan(at("2026-11-16 11:40"), "2026-11-15T23:00Z") == ("2026-11-16T15:00Z", 200 * 60)


def test_trigger_too_far_from_the_slot_does_nothing():
    assert plan(at("2026-11-16 07:25"), "2026-11-15T23:00Z") == ("", 0.0)


def test_late_trigger_runs_a_missed_slot_at_once():
    assert plan(at("2026-11-16 17:10"), "2026-11-15T23:00Z") == ("2026-11-16T15:00Z", 0.0)


def test_slot_already_done_moves_to_the_next_one():
    assert plan(at("2026-11-16 17:40"), "2026-11-16T15:00Z") == ("2026-11-16T23:00Z", 320 * 60)
    assert plan(at("2026-11-16 15:20"), "2026-11-16T15:00Z") == ("", 0.0)


def test_slot_missed_by_more_than_the_grace_is_skipped():
    assert plan(at("2026-11-16 19:30"), "2026-11-15T23:00Z") == ("2026-11-16T23:00Z", 210 * 60)


def test_evening_slot_after_midnight_utc():
    assert plan(at("2026-11-17 01:00"), "2026-11-16T15:00Z") == ("2026-11-16T23:00Z", 0.0)
