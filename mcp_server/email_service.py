"""Email sending via Brevo's transactional email HTTP API.

Render blocks outbound SMTP connections (port 587/465) from its web
services — a direct smtplib connection to smtp.gmail.com fails there with
"OSError: [Errno 101] Network is unreachable" no matter how correct the
credentials are, even though the exact same code works from a local
machine. Sending over HTTPS to Brevo's API sidesteps that block entirely,
since outbound HTTPS is how this service already talks to Groq/Supabase/etc.

If Brevo isn't configured, send_email returns False rather than raising, so
the send_appointment_confirmation_email tool can report the outcome to the
LLM without crashing the request.
"""
from __future__ import annotations

import logging
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
BREVO_SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL")
BREVO_SENDER_NAME = os.environ.get("BREVO_SENDER_NAME", "MediFlow AI")
REQUEST_TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2

# Errors worth retrying: connection-level hiccups and 5xx (server-side,
# possibly transient) responses. 4xx responses (bad API key, unverified
# sender, malformed request) won't succeed on retry.
_TRANSIENT_ERRORS = (httpx.TransportError, httpx.TimeoutException)


def is_configured() -> bool:
    return bool(BREVO_API_KEY and BREVO_SENDER_EMAIL)


def send_email(to_email: str, subject: str, body: str, html_body: str | None = None) -> bool:
    """Sends a transactional email via Brevo; html_body is used if given, else body.

    Transient errors (connection issues, 5xx) are retried up to MAX_ATTEMPTS
    times with backoff; on final failure this logs the real error and
    returns False rather than raising, so a flaky send never breaks the
    booking it's for.
    """
    if not is_configured():
        logger.warning("send_email to %s skipped: BREVO_API_KEY/BREVO_SENDER_EMAIL not configured", to_email)
        return False

    payload = {
        "sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_body or f"<pre>{body}</pre>",
        "textContent": body,
    }
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(
                BREVO_API_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
            if response.status_code >= 500:
                response.raise_for_status()
            if response.status_code >= 400:
                logger.error(
                    "send_email to %s failed: Brevo returned %d: %s",
                    to_email, response.status_code, response.text,
                )
                return False
            return True
        except (_TRANSIENT_ERRORS, httpx.HTTPStatusError) as exc:
            if attempt == MAX_ATTEMPTS:
                logger.exception(
                    "send_email to %s failed permanently after %d attempt(s)", to_email, attempt
                )
                return False
            logger.warning(
                "send_email to %s failed on attempt %d/%d (%s: %s) — retrying in %ds",
                to_email, attempt, MAX_ATTEMPTS, type(exc).__name__, exc,
                RETRY_BACKOFF_SECONDS * attempt,
            )
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    return False
