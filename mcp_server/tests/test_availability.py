"""Unit tests for the pure scheduling logic in db.py — no live Postgres
needed, these exercise compute_free_slots/has_conflict directly.

Run with: pytest mcp_server/tests
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import _normalize_doctor_query, compute_free_slots, has_conflict  # noqa: E402
from server import _parse_date  # noqa: E402


def test_compute_free_slots_empty_day_covers_full_range():
    day = dt.date(2026, 7, 27)
    slots = compute_free_slots(dt.time(9, 0), dt.time(11, 0), [], day, slot_minutes=30)
    assert slots == [
        "2026-07-27T09:00:00",
        "2026-07-27T09:30:00",
        "2026-07-27T10:00:00",
        "2026-07-27T10:30:00",
    ]


def test_compute_free_slots_excludes_booked_range():
    day = dt.date(2026, 7, 27)
    booked = [{"scheduled_at": dt.datetime(2026, 7, 27, 9, 30), "duration_minutes": 30}]
    slots = compute_free_slots(dt.time(9, 0), dt.time(10, 30), booked, day, slot_minutes=30)
    assert slots == ["2026-07-27T09:00:00", "2026-07-27T10:00:00"]


def test_compute_free_slots_partial_overlap_still_blocks_slot():
    # A 45-minute booking straddling two 30-minute slots should block both.
    day = dt.date(2026, 7, 27)
    booked = [{"scheduled_at": dt.datetime(2026, 7, 27, 9, 15), "duration_minutes": 45}]
    slots = compute_free_slots(dt.time(9, 0), dt.time(10, 30), booked, day, slot_minutes=30)
    assert slots == ["2026-07-27T10:00:00"]


def test_has_conflict_detects_overlap():
    booked = [{"scheduled_at": dt.datetime(2026, 7, 27, 10, 0), "duration_minutes": 30}]
    assert has_conflict(dt.datetime(2026, 7, 27, 10, 15), 30, booked) is True


def test_has_conflict_adjacent_slots_do_not_conflict():
    booked = [{"scheduled_at": dt.datetime(2026, 7, 27, 10, 0), "duration_minutes": 30}]
    assert has_conflict(dt.datetime(2026, 7, 27, 10, 30), 30, booked) is False


def test_has_conflict_excludes_own_current_slot_when_rescheduling():
    current = dt.datetime(2026, 7, 27, 10, 0)
    booked = [{"scheduled_at": current, "duration_minutes": 30}]
    # Rescheduling to the exact same slot it already occupies isn't a conflict.
    assert has_conflict(current, 30, booked, exclude_scheduled_at=current) is False


def test_has_conflict_still_catches_conflict_with_other_appointments_when_rescheduling():
    current = dt.datetime(2026, 7, 27, 10, 0)
    other = dt.datetime(2026, 7, 27, 11, 0)
    booked = [
        {"scheduled_at": current, "duration_minutes": 30},
        {"scheduled_at": other, "duration_minutes": 30},
    ]
    assert has_conflict(other, 30, booked, exclude_scheduled_at=current) is True


def test_normalize_doctor_query_treats_title_and_punctuation_variants_as_equal():
    variants = ["ahuja", "dr ahuja", "Dr. Ahuja", "DR AHUJA", "dr. ahuja", "  Ahuja  "]
    normalized = {_normalize_doctor_query(v) for v in variants}
    assert normalized == {"ahuja"}


def test_normalize_doctor_query_only_strips_leading_dr_title():
    # "dr" appearing mid-name (e.g. a doctor actually named "Dravid") must
    # not be stripped — only a leading "Dr"/"Dr." title token should be.
    assert _normalize_doctor_query("Dr. Dravid") == "dravid"


class _FixedDate(dt.date):
    """Lets us pin dt.date.today() inside server._parse_date for these tests."""

    _fixed: dt.date

    @classmethod
    def today(cls):
        return cls._fixed


def _with_fixed_today(monkeypatch, fixed: dt.date):
    _FixedDate._fixed = fixed
    monkeypatch.setattr(dt, "date", _FixedDate)


def test_parse_date_bare_weekday_name_resolves_to_nearest_upcoming_occurrence(monkeypatch):
    # Regression test: a patient asking for "Wednesday" while today is Monday
    # must resolve to the upcoming Wednesday, not silently fall back to today.
    _with_fixed_today(monkeypatch, dt.date(2026, 7, 27))  # Monday
    assert _parse_date("wednesday") == dt.date(2026, 7, 29)


def test_parse_date_next_weekday_prefix_resolves_same_as_bare_name_mid_week(monkeypatch):
    _with_fixed_today(monkeypatch, dt.date(2026, 7, 27))  # Monday
    assert _parse_date("next wednesday") == dt.date(2026, 7, 29)


def test_parse_date_bare_weekday_on_that_same_day_returns_today(monkeypatch):
    _with_fixed_today(monkeypatch, dt.date(2026, 7, 29))  # Wednesday
    assert _parse_date("wednesday") == dt.date(2026, 7, 29)


def test_parse_date_next_weekday_on_that_same_day_skips_to_following_week(monkeypatch):
    _with_fixed_today(monkeypatch, dt.date(2026, 7, 29))  # Wednesday
    assert _parse_date("next wednesday") == dt.date(2026, 8, 5)


def test_parse_date_this_weekday_prefix_behaves_like_bare_name(monkeypatch):
    _with_fixed_today(monkeypatch, dt.date(2026, 7, 27))  # Monday
    assert _parse_date("this friday") == dt.date(2026, 7, 31)
