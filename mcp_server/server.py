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
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import db
import email_service
import google_calendar

load_dotenv()

mcp = FastMCP("mediflow-ai", stateless_http=True, host="0.0.0.0", port=int(os.environ.get("MCP_SERVER_PORT", 8100)))


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
    doctor = await db.find_doctor_by_name(doctor_name)
    if not doctor:
        return {"error": f"No doctor found matching '{doctor_name}'"}

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
        doctor = await db.find_doctor_by_name(doctor_name)
        if not doctor:
            return {"error": f"No doctor found matching '{doctor_name}'"}
        doctor_id = doctor["id"]

    return await db.get_appointment_stats(doctor_id, start, end, filter_reason)


@mcp.tool()
async def book_appointment(
    doctor_name: str,
    patient_name: str,
    patient_email: str,
    datetime_iso: str,
    reason: str | None = None,
    duration_minutes: int = 30,
) -> dict:
    """Book an appointment for a patient with a doctor at a specific time.

    Always call check_doctor_availability first to confirm the slot is free
    and to get a valid ISO datetime — this tool will refuse to double-book.

    Args:
        doctor_name: Doctor's name (partial match ok).
        patient_name: Patient's full name. A patient record is created if
            one doesn't already exist for this email.
        patient_email: Patient's email address, used as their unique key and
            for the confirmation email.
        datetime_iso: Appointment start time as an ISO datetime string, e.g.
            "2026-07-26T15:00:00".
        reason: Optional reason for the visit, e.g. "fever", "checkup".
        duration_minutes: Appointment length in minutes. Defaults to 30.

    Returns:
        A dict with the created appointment id, confirmed details, and
        whether a Google Calendar event was created.
    """
    doctor = await db.find_doctor_by_name(doctor_name)
    if not doctor:
        return {"error": f"No doctor found matching '{doctor_name}'"}

    try:
        start = dt.datetime.fromisoformat(datetime_iso)
    except ValueError:
        return {"error": f"Could not parse datetime_iso '{datetime_iso}'; expected ISO format"}

    booked = await db.get_booked_slots(doctor["id"], start.date())
    end = start + dt.timedelta(minutes=duration_minutes)
    conflict = any(
        start < (b["scheduled_at"] + dt.timedelta(minutes=b["duration_minutes"]))
        and end > b["scheduled_at"]
        for b in booked
    )
    if conflict:
        return {"error": f"{doctor['name']} is already booked at {datetime_iso}. Check availability again."}

    patient = await db.find_or_create_patient(patient_name, patient_email)

    calendar_event_id = await asyncio.to_thread(
        google_calendar.create_event,
        summary=f"Appointment: {patient['name']} with {doctor['name']}",
        description=reason or "",
        start=start,
        duration_minutes=duration_minutes,
        attendee_email=patient_email,
    )

    appointment = await db.insert_appointment(
        doctor["id"], patient["id"], start, duration_minutes, reason, calendar_event_id
    )

    return {
        "appointment_id": appointment["id"],
        "doctor_name": doctor["name"],
        "patient_name": patient["name"],
        "scheduled_at": appointment["scheduled_at"].isoformat(),
        "duration_minutes": duration_minutes,
        "reason": reason,
        "calendar_event_created": calendar_event_id is not None,
    }


@mcp.tool()
async def send_appointment_confirmation_email(
    patient_email: str, doctor_name: str, datetime_iso: str
) -> dict:
    """Send a booking confirmation email to a patient.

    Call this after book_appointment succeeds.

    Args:
        patient_email: Patient's email address to send the confirmation to.
        doctor_name: Doctor's name to mention in the email.
        datetime_iso: Confirmed appointment time as an ISO datetime string.
    """
    subject = f"Appointment Confirmed with {doctor_name}"
    body = (
        f"Your appointment with {doctor_name} is confirmed for {datetime_iso}.\n\n"
        "— MediFlow-AI"
    )
    sent = await asyncio.to_thread(email_service.send_email, patient_email, subject, body)
    if not sent:
        return {
            "sent": False,
            "message": "Email not sent: SMTP is not configured on the server (SMTP_USER/SMTP_APP_PASSWORD missing).",
        }
    return {"sent": True, "message": f"Confirmation email sent to {patient_email}."}


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

    booked = await db.get_booked_slots(existing["doctor_id"], new_start.date())
    new_end = new_start + dt.timedelta(minutes=existing["duration_minutes"])
    conflict = any(
        b["scheduled_at"] != existing["scheduled_at"]
        and new_start < (b["scheduled_at"] + dt.timedelta(minutes=b["duration_minutes"]))
        and new_end > b["scheduled_at"]
        for b in booked
    )
    if conflict:
        return {"error": f"Doctor is already booked at {new_datetime_iso}. Choose another time."}

    updated = await db.update_appointment_time(appointment_id, new_start)
    return {
        "appointment_id": updated["id"],
        "new_scheduled_at": updated["scheduled_at"].isoformat(),
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
    doctor = await db.find_doctor_by_name(doctor_name)
    if not doctor:
        return {"error": f"No doctor found matching '{doctor_name}'"}

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
    doctor = await db.find_doctor_by_name(doctor_name)
    if not doctor:
        return {"error": f"No doctor found matching '{doctor_name}'"}

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
