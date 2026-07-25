"""MediFlow-AI MCP Server.

This is the MCP "server" in the client/server split: it exposes tools,
resources, and prompts over the Model Context Protocol. It knows nothing
about the LLM or how tools get chosen — that's the agent (client) side.

Run standalone:
    python mcp_server/server.py

Exposes streamable-HTTP transport at http://localhost:8100/mcp
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import db
import email_service
import google_calendar
from email_templates import build_confirmation_email

load_dotenv()

logger = logging.getLogger(__name__)

mcp = FastMCP("mediflow-ai", stateless_http=True, host="0.0.0.0", port=int(os.environ.get("MCP_SERVER_PORT", 8100)))

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLACEHOLDER_TOKENS = {
    "patient_name", "name", "patient", "n/a", "na", "unknown", "reason",
    "email", "patient_email", "test", "xxx", "tbd", "none", "-",
}


def _validate_booking_fields(patient_name: str, patient_email: str, reason: str) -> list[str]:
    """Returns a list of human-readable descriptions of missing/invalid
    required fields, or an empty list if everything checks out. Guards
    against the LLM booking with blank or placeholder values instead of
    asking the patient for real information.
    """
    missing: list[str] = []

    name = (patient_name or "").strip()
    if len(name) < 2 or name.lower() in _PLACEHOLDER_TOKENS:
        missing.append("the patient's full name")

    email = (patient_email or "").strip()
    if not _EMAIL_RE.match(email) or email.lower() in _PLACEHOLDER_TOKENS:
        missing.append("a valid email address")

    reason_v = (reason or "").strip()
    if len(reason_v) < 2 or reason_v.lower() in _PLACEHOLDER_TOKENS:
        missing.append("the reason for the visit")

    return missing


async def _resolve_doctor(doctor_name: str) -> tuple[dict | None, dict | None]:
    """Resolve a doctor name query to a single doctor record.

    Returns (doctor, None) on success, or (None, error_dict) when the name
    doesn't match any doctor or matches more than one — in the ambiguous
    case the error tells the LLM to ask the patient which doctor they mean
    rather than guessing, and lists the full names to choose from.
    """
    matches = await db.find_doctors_by_name(doctor_name)
    if not matches:
        return None, {"error": f"No doctor found matching '{doctor_name}'"}
    if len(matches) > 1:
        names = ", ".join(m["name"] for m in matches)
        return None, {
            "error": (
                f"'{doctor_name}' matches more than one doctor: {names}. "
                "Ask the patient which one they mean (using the exact name "
                "shown) before continuing."
            )
        }
    return matches[0], None


def _parse_date(value: str) -> dt.date:
    """Accepts 'today', 'tomorrow', 'yesterday', or an ISO date string."""
    value = value.strip().lower()
    today = dt.date.today()
    if value == "today":
        return today
    if value == "tomorrow":
        return today + dt.timedelta(days=1)
    if value == "yesterday":
        return today - dt.timedelta(days=1)
    return dt.date.fromisoformat(value)


@mcp.tool()
async def check_doctor_availability(doctor_name: str, date: str, time_window: str | None = None) -> dict:
    """Check a doctor's free appointment slots on a given date.

    Args:
        doctor_name: Doctor's name, e.g. "Dr. Ahuja" (partial match ok, e.g. "Ahuja").
        date: The date to check. Accepts "today", "tomorrow", "yesterday", or an
            ISO date string like "2026-07-26".
        time_window: Optional filter, one of "morning" (before 12:00),
            "afternoon" (12:00-17:00), or "evening" (after 17:00). Omit for the
            full day.

    Returns:
        A dict with the doctor's name, working hours, and a list of free
        slot start times (ISO datetime strings).
    """
    doctor, doctor_error = await _resolve_doctor(doctor_name)
    if doctor_error:
        return doctor_error

    target_day = _parse_date(date)
    booked = await db.get_booked_slots(doctor["id"], target_day)
    free_slots = db.compute_free_slots(
        doctor["working_hours_start"], doctor["working_hours_end"], booked, target_day
    )

    if time_window:
        tw = time_window.strip().lower()
        def in_window(iso: str) -> bool:
            hour = dt.datetime.fromisoformat(iso).hour
            if tw == "morning":
                return hour < 12
            if tw == "afternoon":
                return 12 <= hour < 17
            if tw == "evening":
                return hour >= 17
            return True
        free_slots = [s for s in free_slots if in_window(s)]

    return {
        "doctor_name": doctor["name"],
        "date": target_day.isoformat(),
        "working_hours": f"{doctor['working_hours_start']}-{doctor['working_hours_end']}",
        "free_slots": free_slots,
    }


@mcp.tool()
async def get_appointment_stats(
    doctor_name: str | None = None,
    date_range: str = "today",
    filter_reason: str | None = None,
) -> dict:
    """Get appointment counts and details over a date range, optionally filtered.

    Useful for questions like "how many patients visited yesterday" or
    "how many appointments with fever this week".

    Args:
        doctor_name: Optional doctor name to filter by (partial match ok).
            Omit to include all doctors.
        date_range: One of "today", "yesterday", "tomorrow", "this_week",
            or an explicit "YYYY-MM-DD:YYYY-MM-DD" range.
        filter_reason: Optional substring filter on appointment reason,
            e.g. "fever".

    Returns:
        A dict with the total count and a list of matching appointments.
    """
    today = dt.date.today()
    dr = date_range.strip().lower()

    if dr == "today":
        start = end = today
    elif dr == "yesterday":
        start = end = today - dt.timedelta(days=1)
    elif dr == "tomorrow":
        start = end = today + dt.timedelta(days=1)
    elif dr == "this_week":
        start = today - dt.timedelta(days=today.weekday())
        end = start + dt.timedelta(days=6)
    elif ":" in date_range:
        start_str, end_str = date_range.split(":", 1)
        start = dt.date.fromisoformat(start_str.strip())
        end = dt.date.fromisoformat(end_str.strip())
    else:
        start = end = dt.date.fromisoformat(dr)

    doctor_id = None
    if doctor_name:
        doctor, doctor_error = await _resolve_doctor(doctor_name)
        if doctor_error:
            return doctor_error
        doctor_id = doctor["id"]

    return await db.get_appointment_stats(doctor_id, start, end, filter_reason)


@mcp.tool()
async def book_appointment(
    doctor_name: str,
    patient_name: str,
    patient_email: str,
    datetime_iso: str,
    reason: str,
    duration_minutes: int = 30,
) -> dict:
    """Book an appointment for a patient with a doctor at a specific time.

    Always call check_doctor_availability first to confirm the slot is free
    and to get a valid ISO datetime — this tool will refuse to double-book.

    patient_name, patient_email, and reason are all mandatory and must come
    directly from the patient — do not invent, guess, or pass placeholder
    values. If any is missing or invalid, this tool returns an error instead
    of booking; ask the patient for the missing information and call this
    tool again once they've provided it.

    Args:
        doctor_name: Doctor's name (partial match ok).
        patient_name: Patient's full name, as given by the patient. A patient
            record is created if one doesn't already exist for this email.
        patient_email: Patient's real email address, used as their unique key
            and for the confirmation email.
        datetime_iso: Appointment start time as an ISO datetime string, e.g.
            "2026-07-26T15:00:00".
        reason: Reason for the visit, as given by the patient, e.g. "fever",
            "checkup".
        duration_minutes: Appointment length in minutes. Defaults to 30.

    Returns:
        A dict with the created appointment id, confirmed details, and
        whether a Google Calendar event was created.
    """
    missing = _validate_booking_fields(patient_name, patient_email, reason)
    if missing:
        return {
            "error": (
                "Cannot book yet — missing or invalid required information: "
                f"{', '.join(missing)}. Ask the patient for this directly "
                "(do not guess or use placeholder values) and call "
                "book_appointment again once they've replied."
            )
        }

    doctor, doctor_error = await _resolve_doctor(doctor_name)
    if doctor_error:
        return doctor_error

    try:
        start = dt.datetime.fromisoformat(datetime_iso)
    except ValueError:
        return {"error": f"Could not parse datetime_iso '{datetime_iso}'; expected ISO format"}

    patient = await db.find_or_create_patient(patient_name, patient_email)

    # Conflict-check + insert happen atomically under a per-doctor advisory
    # lock (db.book_appointment_atomic), so two near-simultaneous bookings
    # for the same slot can't both slip past the check.
    appointment = await db.book_appointment_atomic(
        doctor["id"], patient["id"], start, duration_minutes, reason
    )
    if appointment is None:
        return {"error": f"{doctor['name']} is already booked at {datetime_iso}. Check availability again."}

    try:
        calendar_event = await asyncio.to_thread(
            google_calendar.create_event,
            summary=f"Appointment: {patient['name']} with {doctor['name']}",
            description=reason or "",
            start=start,
            duration_minutes=duration_minutes,
            attendee_email=patient_email,
        )
    except Exception:
        # Best-effort only — Postgres above is already the source of truth
        # for the booking, so a Google Calendar/OAuth failure must not
        # unwind it.
        logger.exception("Google Calendar event creation failed; booking without it")
        calendar_event = None
    if calendar_event and calendar_event.get("id"):
        await db.set_calendar_event_id(appointment["id"], calendar_event["id"])

    # Confirmation email is sent here, unconditionally on a successful
    # booking, rather than left as a tool the LLM has to remember to call
    # afterwards — that made delivery dependent on model discretion.
    email_sent = False
    if patient_email:
        subject, text_body, html_body = build_confirmation_email(
            patient_name=patient["name"],
            doctor_name=doctor["name"],
            scheduled_at=appointment["scheduled_at"],
            duration_minutes=duration_minutes,
            reason=reason,
            appointment_id=appointment["id"],
            calendar_link=calendar_event.get("html_link") if calendar_event else None,
        )
        try:
            email_sent = await asyncio.to_thread(
                email_service.send_email,
                patient_email,
                subject,
                text_body,
                html_body,
            )
        except Exception:
            logger.exception("Confirmation email failed; booking stands regardless")

    return {
        "appointment_id": appointment["id"],
        "doctor_name": doctor["name"],
        "patient_name": patient["name"],
        "scheduled_at": appointment["scheduled_at"].isoformat(),
        "duration_minutes": duration_minutes,
        "reason": reason,
        "calendar_event_created": calendar_event is not None,
        "calendar_event_link": calendar_event.get("html_link") if calendar_event else None,
        "confirmation_email_sent": email_sent,
    }


@mcp.tool()
async def send_appointment_confirmation_email(appointment_id: int) -> dict:
    """Manually (re)send a booking confirmation email for an existing appointment.

    book_appointment already sends this automatically on success — only
    call this tool if the patient explicitly asks for the email to be
    resent (e.g. "I didn't get the confirmation email"). Details (patient
    name, email, doctor, time, reason) are pulled from the stored
    appointment record, not re-typed by you, so they always match what was
    actually booked.

    Args:
        appointment_id: The id of the appointment to resend a confirmation for.
    """
    appt = await db.get_appointment_by_id(appointment_id)
    if not appt:
        return {"error": f"No appointment found with id {appointment_id}"}
    if not appt["patient_email"]:
        return {"error": "This appointment has no patient email on file to send to."}

    subject, text_body, html_body = build_confirmation_email(
        patient_name=appt["patient_name"],
        doctor_name=appt["doctor_name"],
        scheduled_at=appt["scheduled_at"],
        duration_minutes=appt["duration_minutes"],
        reason=appt["reason"] or "General consultation",
        appointment_id=appt["id"],
    )
    sent = await asyncio.to_thread(
        email_service.send_email, appt["patient_email"], subject, text_body, html_body
    )
    if not sent:
        return {
            "sent": False,
            "message": "Email not sent: SMTP is not configured on the server (SMTP_USER/SMTP_APP_PASSWORD missing).",
        }
    return {"sent": True, "message": f"Confirmation email sent to {appt['patient_email']}."}


@mcp.tool()
async def reschedule_appointment(appointment_id: int, new_datetime_iso: str) -> dict:
    """Reschedule an existing appointment to a new date/time.

    Args:
        appointment_id: The id of the appointment to reschedule.
        new_datetime_iso: The new start time as an ISO datetime string.
    """
    existing = await db.get_appointment_by_id(appointment_id)
    if not existing:
        return {"error": f"No appointment found with id {appointment_id}"}

    try:
        new_start = dt.datetime.fromisoformat(new_datetime_iso)
    except ValueError:
        return {"error": f"Could not parse new_datetime_iso '{new_datetime_iso}'; expected ISO format"}

    updated = await db.reschedule_appointment_atomic(
        appointment_id,
        existing["doctor_id"],
        existing["scheduled_at"],
        existing["duration_minutes"],
        new_start,
    )
    if updated is None:
        return {"error": f"Doctor is already booked at {new_datetime_iso}. Choose another time."}

    return {
        "appointment_id": updated["id"],
        "new_scheduled_at": updated["scheduled_at"].isoformat(),
    }


@mcp.tool()
async def suggest_reschedule(doctor_name: str, originally_requested_datetime: str) -> dict:
    """Suggest nearby available slots when a requested booking time isn't free.

    Call this after book_appointment fails because the slot is taken — do
    not guess alternative times yourself; this tool checks real availability.

    Args:
        doctor_name: Doctor's name (partial match ok).
        originally_requested_datetime: The ISO datetime the patient originally
            asked for, e.g. "2026-07-26T15:00:00". Suggestions are ranked by
            closeness to this time.

    Returns:
        A dict with up to 3 suggested ISO datetime strings, nearest first.
    """
    doctor, doctor_error = await _resolve_doctor(doctor_name)
    if doctor_error:
        return doctor_error

    try:
        requested = dt.datetime.fromisoformat(originally_requested_datetime)
    except ValueError:
        return {"error": f"Could not parse originally_requested_datetime '{originally_requested_datetime}'; expected ISO format"}

    candidates: list[str] = []
    for day_offset in range(0, 7):
        target_day = requested.date() + dt.timedelta(days=day_offset)
        booked = await db.get_booked_slots(doctor["id"], target_day)
        candidates.extend(
            db.compute_free_slots(doctor["working_hours_start"], doctor["working_hours_end"], booked, target_day)
        )
        if len(candidates) >= 3:
            break

    candidates.sort(key=lambda iso: abs((dt.datetime.fromisoformat(iso) - requested).total_seconds()))

    return {
        "doctor_name": doctor["name"],
        "originally_requested_datetime": originally_requested_datetime,
        "suggested_slots": candidates[:3],
    }


@mcp.tool()
async def notify_doctor(doctor_name: str, message: str, channel: str = "in_app") -> dict:
    """Send a notification to a doctor (e.g. about a new booking or urgent case).

    Args:
        doctor_name: Doctor's name (partial match ok).
        message: The notification text.
        channel: Delivery channel, "in_app" (default, stored for the doctor
            dashboard) or "slack" (also posts to a Slack webhook if configured).
    """
    doctor, doctor_error = await _resolve_doctor(doctor_name)
    if doctor_error:
        return doctor_error

    notification = await db.insert_notification(doctor["id"], message, channel)

    slack_posted = False
    if channel == "slack":
        slack_posted = await asyncio.to_thread(_post_to_slack, doctor["name"], message)

    return {
        "notification_id": notification["id"],
        "doctor_name": doctor["name"],
        "channel": channel,
        "slack_posted": slack_posted,
    }


def _post_to_slack(doctor_name: str, message: str) -> bool:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return False
    import httpx

    resp = httpx.post(webhook_url, json={"text": f"[{doctor_name}] {message}"}, timeout=10)
    return resp.status_code == 200


@mcp.resource("doctor-schedule://{doctor_name}/{date}")
async def doctor_schedule_resource(doctor_name: str, date: str) -> dict:
    """Raw appointment schedule for a doctor on a given date (read-only context, not an action)."""
    doctor, doctor_error = await _resolve_doctor(doctor_name)
    if doctor_error:
        return doctor_error

    target_day = _parse_date(date)
    booked = await db.get_booked_slots(doctor["id"], target_day)
    return {
        "doctor_name": doctor["name"],
        "date": target_day.isoformat(),
        "appointments": [
            {
                "scheduled_at": b["scheduled_at"].isoformat(),
                "duration_minutes": b["duration_minutes"],
            }
            for b in booked
        ],
    }


@mcp.resource("appointment://{appointment_id}")
async def appointment_resource(appointment_id: str) -> dict:
    """Raw details for a single appointment by id (read-only context, not an action)."""
    appt = await db.get_appointment_by_id(int(appointment_id))
    if not appt:
        return {"error": f"No appointment found with id {appointment_id}"}
    return {
        "id": appt["id"],
        "doctor_name": appt["doctor_name"],
        "patient_name": appt["patient_name"],
        "patient_email": appt["patient_email"],
        "scheduled_at": appt["scheduled_at"].isoformat(),
        "duration_minutes": appt["duration_minutes"],
        "status": appt["status"],
        "reason": appt["reason"],
    }


@mcp.prompt()
def summarize_doctor_day(doctor_name: str, date: str = "today") -> str:
    """Reusable prompt template: ask the assistant to summarize a doctor's day in plain language."""
    return (
        f"Look up {doctor_name}'s appointments for {date} (use get_appointment_stats "
        f"scoped to that doctor and date) and write a short, friendly summary: how "
        f"many patients, what times, and call out any notable patterns (e.g. several "
        f"patients with the same reason)."
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
