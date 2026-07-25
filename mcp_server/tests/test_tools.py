"""Tests for the MCP tool functions in server.py — the agentic core that
test_availability.py doesn't touch (it only covers the pure helpers in db.py).

These monkeypatch the `db` / `google_calendar` / `email_service` modules
instead of hitting a live Postgres/Google/SMTP, so they run anywhere.
`@mcp.tool()` returns the original function unchanged (verified against the
installed `mcp` SDK), so `server.check_doctor_availability` etc. are plain
importable async callables.

Run with: pytest mcp_server/tests
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402
import db  # noqa: E402
import email_service  # noqa: E402
import google_calendar  # noqa: E402


def run(coro):
    return asyncio.run(coro)


AHUJA = {"id": 1, "name": "Dr. Ahuja", "working_hours_start": dt.time(9, 0), "working_hours_end": dt.time(17, 0)}
MEHTA = {"id": 2, "name": "Dr. Mehta", "working_hours_start": dt.time(10, 0), "working_hours_end": dt.time(18, 0)}


# --- _validate_booking_fields -------------------------------------------------

def test_validate_booking_fields_accepts_real_values():
    assert server._validate_booking_fields("Jane Doe", "jane@example.com", "fever") == []


def test_validate_booking_fields_rejects_placeholder_values():
    missing = server._validate_booking_fields("patient_name", "email", "reason")
    assert "the patient's full name" in missing
    assert "a valid email address" in missing
    assert "the reason for the visit" in missing


def test_validate_booking_fields_rejects_malformed_email():
    missing = server._validate_booking_fields("Jane Doe", "not-an-email", "checkup")
    assert missing == ["a valid email address"]


# --- _resolve_doctor -----------------------------------------------------------

def test_resolve_doctor_no_match_returns_error(monkeypatch):
    async def fake_find(name):
        return []

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    doctor, error = run(server._resolve_doctor("Nobody"))
    assert doctor is None
    assert "No doctor found" in error["error"]


def test_resolve_doctor_ambiguous_match_asks_to_disambiguate(monkeypatch):
    async def fake_find(name):
        return [AHUJA, MEHTA]

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    doctor, error = run(server._resolve_doctor("D"))
    assert doctor is None
    assert "Dr. Ahuja" in error["error"] and "Dr. Mehta" in error["error"]


def test_resolve_doctor_single_match_returns_doctor(monkeypatch):
    async def fake_find(name):
        return [AHUJA]

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    doctor, error = run(server._resolve_doctor("Ahuja"))
    assert error is None
    assert doctor == AHUJA


# --- check_doctor_availability ---------------------------------------------

def test_check_doctor_availability_filters_by_time_window(monkeypatch):
    async def fake_find(name):
        return [AHUJA]

    async def fake_booked(doctor_id, day):
        return []

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    monkeypatch.setattr(db, "get_booked_slots", fake_booked)

    result = run(server.check_doctor_availability("Ahuja", "2026-07-27", time_window="morning"))
    assert result["doctor_name"] == "Dr. Ahuja"
    assert all(dt.datetime.fromisoformat(s).hour < 12 for s in result["free_slots"])
    assert len(result["free_slots"]) > 0


def test_check_doctor_availability_unknown_doctor_propagates_error(monkeypatch):
    async def fake_find(name):
        return []

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    result = run(server.check_doctor_availability("Nobody", "today"))
    assert "error" in result


# --- book_appointment --------------------------------------------------------

def test_book_appointment_rejects_missing_patient_info():
    result = run(
        server.book_appointment(
            doctor_name="Ahuja",
            patient_name="",
            patient_email="",
            datetime_iso="2026-07-27T10:00:00",
            reason="",
        )
    )
    assert "error" in result
    assert "Cannot book yet" in result["error"]


def test_book_appointment_reports_slot_already_taken(monkeypatch):
    async def fake_find(name):
        return [AHUJA]

    async def fake_find_or_create(name, email):
        return {"id": 10, "name": name}

    async def fake_book_atomic(doctor_id, patient_id, start, duration, reason):
        return None  # slot no longer free

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    monkeypatch.setattr(db, "find_or_create_patient", fake_find_or_create)
    monkeypatch.setattr(db, "book_appointment_atomic", fake_book_atomic)

    result = run(
        server.book_appointment(
            doctor_name="Ahuja",
            patient_name="Jane Doe",
            patient_email="jane@example.com",
            datetime_iso="2026-07-27T10:00:00",
            reason="fever",
        )
    )
    assert "error" in result
    assert "already booked" in result["error"]


def test_book_appointment_success_path_without_calendar_or_email(monkeypatch):
    """Calendar/email unconfigured -> booking still succeeds, flags reflect that."""
    async def fake_find(name):
        return [AHUJA]

    async def fake_find_or_create(name, email):
        return {"id": 10, "name": name}

    async def fake_book_atomic(doctor_id, patient_id, start, duration, reason):
        return {
            "id": 99,
            "scheduled_at": start,
            "duration_minutes": duration,
            "reason": reason,
        }

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    monkeypatch.setattr(db, "find_or_create_patient", fake_find_or_create)
    monkeypatch.setattr(db, "book_appointment_atomic", fake_book_atomic)
    monkeypatch.setattr(google_calendar, "create_event", lambda **kwargs: None)
    monkeypatch.setattr(email_service, "send_email", lambda *a, **kw: False)

    result = run(
        server.book_appointment(
            doctor_name="Ahuja",
            patient_name="Jane Doe",
            patient_email="jane@example.com",
            datetime_iso="2026-07-27T10:00:00",
            reason="fever",
        )
    )
    assert result["appointment_id"] == 99
    assert result["calendar_event_created"] is False
    assert result["confirmation_email_sent"] is False


def test_book_appointment_success_path_sends_email_and_creates_event(monkeypatch):
    async def fake_find(name):
        return [AHUJA]

    async def fake_find_or_create(name, email):
        return {"id": 10, "name": name}

    async def fake_book_atomic(doctor_id, patient_id, start, duration, reason):
        return {"id": 100, "scheduled_at": start, "duration_minutes": duration, "reason": reason}

    async def fake_set_calendar_event_id(appointment_id, event_id):
        pass

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    monkeypatch.setattr(db, "find_or_create_patient", fake_find_or_create)
    monkeypatch.setattr(db, "book_appointment_atomic", fake_book_atomic)
    monkeypatch.setattr(db, "set_calendar_event_id", fake_set_calendar_event_id)
    monkeypatch.setattr(
        google_calendar, "create_event", lambda **kwargs: {"id": "evt_1", "html_link": "https://cal/evt_1"}
    )
    monkeypatch.setattr(email_service, "send_email", lambda *a, **kw: True)

    result = run(
        server.book_appointment(
            doctor_name="Ahuja",
            patient_name="Jane Doe",
            patient_email="jane@example.com",
            datetime_iso="2026-07-27T10:00:00",
            reason="fever",
        )
    )
    assert result["calendar_event_created"] is True
    assert result["calendar_event_link"] == "https://cal/evt_1"
    assert result["confirmation_email_sent"] is True


def test_book_appointment_calendar_failure_does_not_break_booking(monkeypatch):
    """A Google Calendar exception must not unwind the Postgres booking."""
    async def fake_find(name):
        return [AHUJA]

    async def fake_find_or_create(name, email):
        return {"id": 10, "name": name}

    async def fake_book_atomic(doctor_id, patient_id, start, duration, reason):
        return {"id": 101, "scheduled_at": start, "duration_minutes": duration, "reason": reason}

    def raising_create_event(**kwargs):
        raise RuntimeError("Google API down")

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    monkeypatch.setattr(db, "find_or_create_patient", fake_find_or_create)
    monkeypatch.setattr(db, "book_appointment_atomic", fake_book_atomic)
    monkeypatch.setattr(google_calendar, "create_event", raising_create_event)
    monkeypatch.setattr(email_service, "send_email", lambda *a, **kw: True)

    result = run(
        server.book_appointment(
            doctor_name="Ahuja",
            patient_name="Jane Doe",
            patient_email="jane@example.com",
            datetime_iso="2026-07-27T10:00:00",
            reason="fever",
        )
    )
    assert result["appointment_id"] == 101
    assert result["calendar_event_created"] is False


# --- get_appointment_stats (date-range parsing lives in server.py, not db.py) --

def test_get_appointment_stats_resolves_this_week_range(monkeypatch):
    captured = {}

    async def fake_stats(doctor_id, start, end, reason_filter):
        captured["doctor_id"] = doctor_id
        captured["start"] = start
        captured["end"] = end
        captured["reason_filter"] = reason_filter
        return {"total": 0, "appointments": []}

    monkeypatch.setattr(db, "get_appointment_stats", fake_stats)

    run(server.get_appointment_stats(date_range="this_week"))

    today = dt.date.today()
    expected_start = today - dt.timedelta(days=today.weekday())
    assert captured["start"] == expected_start
    assert captured["end"] == expected_start + dt.timedelta(days=6)
    assert captured["doctor_id"] is None


def test_get_appointment_stats_resolves_explicit_range_and_doctor(monkeypatch):
    captured = {}

    async def fake_find(name):
        return [AHUJA]

    async def fake_stats(doctor_id, start, end, reason_filter):
        captured.update(doctor_id=doctor_id, start=start, end=end, reason_filter=reason_filter)
        return {"total": 3, "appointments": []}

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    monkeypatch.setattr(db, "get_appointment_stats", fake_stats)

    result = run(
        server.get_appointment_stats(
            doctor_name="Ahuja", date_range="2026-07-01:2026-07-31", filter_reason="fever"
        )
    )
    assert result["total"] == 3
    assert captured["doctor_id"] == AHUJA["id"]
    assert captured["start"] == dt.date(2026, 7, 1)
    assert captured["end"] == dt.date(2026, 7, 31)
    assert captured["reason_filter"] == "fever"


def test_get_appointment_stats_unknown_doctor_short_circuits(monkeypatch):
    async def fake_find(name):
        return []

    called = False

    async def fake_stats(*a, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    monkeypatch.setattr(db, "get_appointment_stats", fake_stats)

    result = run(server.get_appointment_stats(doctor_name="Nobody"))
    assert "error" in result
    assert called is False  # must not query stats for a doctor that doesn't exist


# --- notify_doctor ------------------------------------------------------------

def test_notify_doctor_in_app_does_not_touch_slack(monkeypatch):
    async def fake_find(name):
        return [AHUJA]

    async def fake_insert(doctor_id, message, channel):
        return {"id": 5, "doctor_id": doctor_id, "message": message, "channel": channel}

    slack_called = False

    def fake_post_to_slack(doctor_name, message):
        nonlocal slack_called
        slack_called = True
        return True

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    monkeypatch.setattr(db, "insert_notification", fake_insert)
    monkeypatch.setattr(server, "_post_to_slack", fake_post_to_slack)

    result = run(server.notify_doctor("Ahuja", "Patient running late", channel="in_app"))
    assert result["slack_posted"] is False
    assert slack_called is False


def test_notify_doctor_slack_channel_posts_to_slack(monkeypatch):
    async def fake_find(name):
        return [AHUJA]

    async def fake_insert(doctor_id, message, channel):
        return {"id": 6, "doctor_id": doctor_id, "message": message, "channel": channel}

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    monkeypatch.setattr(db, "insert_notification", fake_insert)
    monkeypatch.setattr(server, "_post_to_slack", lambda doctor_name, message: True)

    result = run(server.notify_doctor("Ahuja", "3 patients with fever today", channel="slack"))
    assert result["slack_posted"] is True


# --- reschedule_appointment ---------------------------------------------------

def test_reschedule_appointment_missing_appointment_returns_error(monkeypatch):
    async def fake_get(appointment_id):
        return None

    monkeypatch.setattr(db, "get_appointment_by_id", fake_get)
    result = run(server.reschedule_appointment(9999, "2026-07-27T10:00:00"))
    assert "error" in result


def test_reschedule_appointment_conflict_returns_error(monkeypatch):
    async def fake_get(appointment_id):
        return {
            "id": appointment_id,
            "doctor_id": AHUJA["id"],
            "scheduled_at": dt.datetime(2026, 7, 27, 9, 0),
            "duration_minutes": 30,
        }

    async def fake_reschedule_atomic(appointment_id, doctor_id, current, duration, new_start):
        return None  # conflict

    monkeypatch.setattr(db, "get_appointment_by_id", fake_get)
    monkeypatch.setattr(db, "reschedule_appointment_atomic", fake_reschedule_atomic)

    result = run(server.reschedule_appointment(1, "2026-07-27T11:00:00"))
    assert "error" in result
    assert "already booked" in result["error"]


# --- suggest_reschedule -------------------------------------------------------

def test_suggest_reschedule_ranks_by_closeness_to_requested_time(monkeypatch):
    async def fake_find(name):
        return [AHUJA]

    async def fake_booked(doctor_id, day):
        return []

    monkeypatch.setattr(db, "find_doctors_by_name", fake_find)
    monkeypatch.setattr(db, "get_booked_slots", fake_booked)

    result = run(server.suggest_reschedule("Ahuja", "2026-07-27T15:00:00"))
    assert len(result["suggested_slots"]) <= 3
    times = [dt.datetime.fromisoformat(s) for s in result["suggested_slots"]]
    requested = dt.datetime(2026, 7, 27, 15, 0)
    deltas = [abs((t - requested).total_seconds()) for t in times]
    assert deltas == sorted(deltas)
