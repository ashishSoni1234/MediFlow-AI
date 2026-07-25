"""HTML/text email templates for patient-facing notifications.

Kept separate from server.py so tool functions stay focused on MCP/booking
logic rather than markup.
"""
from __future__ import annotations

import datetime as dt
import html as html_lib

_BRAND = "MediFlow-AI"
_ACCENT = "#0f766e"


def _format_when(scheduled_at: dt.datetime) -> str:
    date_part = scheduled_at.strftime("%A, %d %B %Y")
    time_part = scheduled_at.strftime("%I:%M %p").lstrip("0") or "12:00 AM"
    return f"{date_part} at {time_part}"


def build_confirmation_email(
    *,
    patient_name: str,
    doctor_name: str,
    scheduled_at: dt.datetime,
    duration_minutes: int,
    reason: str,
    appointment_id: int,
    calendar_link: str | None = None,
) -> tuple[str, str, str]:
    """Returns (subject, text_body, html_body) for a booking confirmation."""
    when = _format_when(scheduled_at)
    subject = f"Appointment Confirmed with {doctor_name}"

    text_body = (
        f"Dear {patient_name},\n\n"
        f"Your appointment has been confirmed. Here are your visit details:\n\n"
        f"  Doctor:          {doctor_name}\n"
        f"  Date & Time:     {when}\n"
        f"  Duration:        {duration_minutes} minutes\n"
        f"  Reason for visit: {reason}\n"
        f"  Appointment ID:  #{appointment_id}\n\n"
    )
    if calendar_link:
        text_body += f"View it in Google Calendar: {calendar_link}\n\n"
    text_body += (
        "Need to reschedule or cancel? Just reply to this email or let our "
        "assistant know.\n\n"
        "Warm regards,\n"
        f"The {_BRAND} Team\n"
    )

    e_name = html_lib.escape(patient_name)
    e_doctor = html_lib.escape(doctor_name)
    e_reason = html_lib.escape(reason)

    calendar_button = ""
    if calendar_link:
        calendar_button = (
            f'<a href="{html_lib.escape(calendar_link)}" '
            f'style="display:inline-block;background:{_ACCENT};color:#ffffff;'
            f'text-decoration:none;padding:10px 22px;border-radius:8px;'
            f'font-size:14px;font-weight:600;margin-top:8px;">'
            f"View in Google Calendar</a>"
        )

    html_body = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:560px;margin:0 auto;color:#1f2937;">
  <div style="background:linear-gradient(135deg,#0f766e,#14b8a6);padding:24px 32px;border-radius:12px 12px 0 0;">
    <h1 style="margin:0;color:#ffffff;font-size:20px;letter-spacing:0.3px;">{_BRAND}</h1>
    <p style="margin:4px 0 0;color:#d1fae5;font-size:13px;">Appointment Confirmation</p>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;border-radius:0 0 12px 12px;padding:32px;">
    <p style="font-size:15px;margin-top:0;">Dear {e_name},</p>
    <p style="font-size:15px;">Your appointment has been <strong style="color:{_ACCENT};">confirmed</strong>. Here are your visit details:</p>
    <table role="presentation" style="width:100%;border-collapse:collapse;margin:20px 0;font-size:14px;">
      <tr><td style="padding:8px 0;color:#6b7280;width:150px;">Doctor</td><td style="padding:8px 0;font-weight:600;">{e_doctor}</td></tr>
      <tr style="border-top:1px solid #f3f4f6;"><td style="padding:8px 0;color:#6b7280;">Date &amp; Time</td><td style="padding:8px 0;font-weight:600;">{when}</td></tr>
      <tr style="border-top:1px solid #f3f4f6;"><td style="padding:8px 0;color:#6b7280;">Duration</td><td style="padding:8px 0;">{duration_minutes} minutes</td></tr>
      <tr style="border-top:1px solid #f3f4f6;"><td style="padding:8px 0;color:#6b7280;">Reason for visit</td><td style="padding:8px 0;">{e_reason}</td></tr>
      <tr style="border-top:1px solid #f3f4f6;"><td style="padding:8px 0;color:#6b7280;">Appointment ID</td><td style="padding:8px 0;">#{appointment_id}</td></tr>
    </table>
    {calendar_button}
    <p style="font-size:13px;color:#6b7280;margin-top:28px;">Need to reschedule or cancel? Just reply to this email or ask our assistant.</p>
    <p style="font-size:14px;margin-bottom:0;">Warm regards,<br><strong>The {_BRAND} Team</strong></p>
  </div>
  <p style="text-align:center;font-size:11px;color:#9ca3af;margin-top:16px;">This is an automated message from {_BRAND}. Please do not reply if you did not request this appointment.</p>
</div>
"""
    return subject, text_body, html_body
