"""Postgres access layer for the MCP server.

Keeps all SQL in one place so the tool functions in server.py stay
focused on MCP concerns (schemas, descriptions) rather than queries.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timedelta
from typing import Any

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://mediflow:mediflow@localhost:5433/mediflow"
)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool


def _normalize_doctor_query(name: str) -> str:
    """Strip title prefixes and punctuation so 'ahuja', 'dr ahuja',
    'Dr. Ahuja', and 'DR AHUJA' all normalize to the same string."""
    normalized = name.strip().lower()
    normalized = re.sub(r"^dr\.?\s+", "", normalized)
    normalized = re.sub(r"[.,]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


async def find_doctors_by_name(name: str) -> list[dict[str, Any]]:
    """Find every doctor whose name matches the query, tolerant of 'Dr.'
    prefixes, punctuation, and case. Returns all matches (not just the
    first) so callers can ask the patient to disambiguate when a query
    like 'sharma' matches more than one doctor.
    """
    query = _normalize_doctor_query(name)
    if not query:
        return []

    pool = await get_pool()
    rows = await pool.fetch("SELECT * FROM doctors")
    matches = [
        dict(row)
        for row in rows
        if query == (doctor_norm := _normalize_doctor_query(row["name"]))
        or query in doctor_norm
        or doctor_norm in query
    ]
    return matches


async def find_or_create_patient(name: str, email: str | None, phone: str | None = None) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM patients WHERE email = $1", email)
    if row:
        return dict(row)
    row = await pool.fetchrow(
        "INSERT INTO patients (name, email, phone) VALUES ($1, $2, $3) RETURNING *",
        name,
        email,
        phone,
    )
    return dict(row)


async def get_booked_slots(doctor_id: int, day: date) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT scheduled_at, duration_minutes FROM appointments
        WHERE doctor_id = $1
          AND status != 'cancelled'
          AND scheduled_at::date = $2
        ORDER BY scheduled_at
        """,
        doctor_id,
        day,
    )
    return [dict(r) for r in rows]


def compute_free_slots(
    working_start: time,
    working_end: time,
    booked: list[dict[str, Any]],
    day: date,
    slot_minutes: int = 30,
) -> list[str]:
    """Generate free slot start times (ISO strings) for a day given bookings."""
    booked_ranges = []
    for b in booked:
        start = b["scheduled_at"]
        end = start + timedelta(minutes=b["duration_minutes"])
        booked_ranges.append((start, end))

    slots: list[str] = []
    cursor = datetime.combine(day, working_start)
    day_end = datetime.combine(day, working_end)
    step = timedelta(minutes=slot_minutes)

    while cursor + step <= day_end:
        slot_end = cursor + step
        overlaps = any(cursor < b_end and slot_end > b_start for b_start, b_end in booked_ranges)
        if not overlaps:
            slots.append(cursor.isoformat())
        cursor += step

    return slots


def has_conflict(
    start: datetime,
    duration_minutes: int,
    booked: list[dict[str, Any]],
    exclude_scheduled_at: datetime | None = None,
) -> bool:
    """Pure overlap check shared by booking and rescheduling.

    `exclude_scheduled_at` lets a reschedule ignore the appointment's own
    current slot when checking against the doctor's other bookings.
    """
    end = start + timedelta(minutes=duration_minutes)
    for b in booked:
        if exclude_scheduled_at is not None and b["scheduled_at"] == exclude_scheduled_at:
            continue
        b_end = b["scheduled_at"] + timedelta(minutes=b["duration_minutes"])
        if start < b_end and end > b["scheduled_at"]:
            return True
    return False


async def book_appointment_atomic(
    doctor_id: int,
    patient_id: int,
    scheduled_at: datetime,
    duration_minutes: int,
    reason: str | None,
) -> dict[str, Any] | None:
    """Checks for conflicts and inserts the appointment in one transaction,
    serialized per-doctor with a Postgres advisory lock so two concurrent
    bookings for the same slot can't both pass the conflict check.

    Returns None if the slot is no longer free. `google_calendar_event_id`
    is deliberately not set here — that's a best-effort network call added
    afterwards via `set_calendar_event_id`, kept outside the lock so a slow
    Calendar API doesn't hold the advisory lock open.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", doctor_id)
            rows = await conn.fetch(
                """
                SELECT scheduled_at, duration_minutes FROM appointments
                WHERE doctor_id = $1 AND status != 'cancelled' AND scheduled_at::date = $2
                """,
                doctor_id,
                scheduled_at.date(),
            )
            booked = [dict(r) for r in rows]
            if has_conflict(scheduled_at, duration_minutes, booked):
                return None

            row = await conn.fetchrow(
                """
                INSERT INTO appointments (doctor_id, patient_id, scheduled_at, duration_minutes, reason)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
                """,
                doctor_id,
                patient_id,
                scheduled_at,
                duration_minutes,
                reason,
            )
            return dict(row)


async def set_calendar_event_id(appointment_id: int, google_calendar_event_id: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE appointments SET google_calendar_event_id = $1 WHERE id = $2",
        google_calendar_event_id,
        appointment_id,
    )


async def reschedule_appointment_atomic(
    appointment_id: int,
    doctor_id: int,
    current_scheduled_at: datetime,
    duration_minutes: int,
    new_scheduled_at: datetime,
) -> dict[str, Any] | None:
    """Same locked-transaction pattern as book_appointment_atomic, but for
    moving an existing appointment. Returns None on conflict.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", doctor_id)
            rows = await conn.fetch(
                """
                SELECT scheduled_at, duration_minutes FROM appointments
                WHERE doctor_id = $1 AND status != 'cancelled' AND scheduled_at::date = $2
                """,
                doctor_id,
                new_scheduled_at.date(),
            )
            booked = [dict(r) for r in rows]
            if has_conflict(new_scheduled_at, duration_minutes, booked, exclude_scheduled_at=current_scheduled_at):
                return None

            row = await conn.fetchrow(
                "UPDATE appointments SET scheduled_at = $1 WHERE id = $2 RETURNING *",
                new_scheduled_at,
                appointment_id,
            )
            return dict(row) if row else None


async def get_appointment_stats(
    doctor_id: int | None,
    start: date,
    end: date,
    reason_filter: str | None,
) -> dict[str, Any]:
    pool = await get_pool()
    conditions = ["a.scheduled_at::date >= $1", "a.scheduled_at::date <= $2"]
    params: list[Any] = [start, end]

    if doctor_id is not None:
        conditions.append(f"a.doctor_id = ${len(params) + 1}")
        params.append(doctor_id)
    if reason_filter:
        conditions.append(f"a.reason ILIKE ${len(params) + 1}")
        params.append(f"%{reason_filter}%")

    where_clause = " AND ".join(conditions)

    total = await pool.fetchval(
        f"SELECT count(*) FROM appointments a WHERE {where_clause}", *params
    )
    rows = await pool.fetch(
        f"""
        SELECT a.id, a.scheduled_at, a.status, a.reason, d.name AS doctor_name, p.name AS patient_name
        FROM appointments a
        JOIN doctors d ON d.id = a.doctor_id
        JOIN patients p ON p.id = a.patient_id
        WHERE {where_clause}
        ORDER BY a.scheduled_at
        """,
        *params,
    )

    return {
        "total": total,
        "appointments": [
            {
                "id": r["id"],
                "scheduled_at": r["scheduled_at"].isoformat(),
                "status": r["status"],
                "reason": r["reason"],
                "doctor_name": r["doctor_name"],
                "patient_name": r["patient_name"],
            }
            for r in rows
        ],
    }


async def insert_notification(doctor_id: int, message: str, channel: str) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        "INSERT INTO notifications (doctor_id, message, channel) VALUES ($1, $2, $3) RETURNING *",
        doctor_id,
        message,
        channel,
    )
    return dict(row)


async def get_appointment_by_id(appointment_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT a.*, d.name AS doctor_name, p.name AS patient_name, p.email AS patient_email
        FROM appointments a
        JOIN doctors d ON d.id = a.doctor_id
        JOIN patients p ON p.id = a.patient_id
        WHERE a.id = $1
        """,
        appointment_id,
    )
    return dict(row) if row else None
