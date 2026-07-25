"""Postgres access layer for the MCP server.

Keeps all SQL in one place so the tool functions in server.py stay
focused on MCP concerns (schemas, descriptions) rather than queries.
"""
from __future__ import annotations

import os
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


async def find_doctor_by_name(name: str) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM doctors WHERE name ILIKE $1 OR name ILIKE $2 LIMIT 1",
        name,
        f"%{name}%",
    )
    return dict(row) if row else None


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


async def insert_appointment(
    doctor_id: int,
    patient_id: int,
    scheduled_at: datetime,
    duration_minutes: int,
    reason: str | None,
    google_calendar_event_id: str | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO appointments
            (doctor_id, patient_id, scheduled_at, duration_minutes, reason, google_calendar_event_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        doctor_id,
        patient_id,
        scheduled_at,
        duration_minutes,
        reason,
        google_calendar_event_id,
    )
    return dict(row)


async def update_appointment_time(appointment_id: int, new_scheduled_at: datetime) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow(
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
