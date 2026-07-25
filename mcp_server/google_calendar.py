"""Google Calendar integration for appointment booking.

Uses a long-lived OAuth2 refresh token (simplest for a small demo app —
no service-account domain-wide delegation needed). If credentials aren't
configured, create_event returns None and the caller (book_appointment
tool) still succeeds using Postgres as the source of truth; the calendar
event is best-effort on top of that.
"""
from __future__ import annotations

import datetime as dt
import os

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN)


def _get_service():
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri=_TOKEN_URI,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=_SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def create_event(
    summary: str,
    description: str,
    start: dt.datetime,
    duration_minutes: int,
    attendee_email: str | None = None,
) -> dict | None:
    """Creates a calendar event and returns {"id", "html_link"}, or None if not configured."""
    if not is_configured():
        return None

    service = _get_service()
    end = start + dt.timedelta(minutes=duration_minutes)
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
    }
    if attendee_email:
        body["attendees"] = [{"email": attendee_email}]

    event = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=body, sendUpdates="all").execute()
    return {"id": event.get("id"), "html_link": event.get("htmlLink")}


def delete_event(event_id: str) -> None:
    if not is_configured() or not event_id:
        return
    service = _get_service()
    service.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id).execute()
