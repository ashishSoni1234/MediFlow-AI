"""Email sending via SMTP (Gmail App Password), per Section 1's "simpler fallback".

If SMTP credentials aren't configured, send_email returns False rather than
raising, so the send_appointment_confirmation_email tool can report the
outcome to the LLM without crashing the request.

Gmail's SMTP relay occasionally defers a send with a transient error (e.g.
connection drop, or a temporary 4xx like "421 Try again later" under its
anti-abuse rate limiting) even when credentials and network are fine. A single
attempt treats that the same as a permanent failure, so send_email retries a
bounded number of times with backoff before giving up.
"""
from __future__ import annotations

import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD")
SMTP_TIMEOUT_SECONDS = 15
SMTP_MAX_ATTEMPTS = 3
SMTP_RETRY_BACKOFF_SECONDS = 2

# Errors worth retrying: connection-level hiccups and SMTP 4xx (temporary,
# server-requested retry) replies. SMTPAuthenticationError and other 5xx
# (permanent) failures are not retried — retrying won't fix a bad password.
_TRANSIENT_SMTP_ERRORS = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    smtplib.SMTPResponseException,
    TimeoutError,
    OSError,
)


def is_configured() -> bool:
    return bool(SMTP_USER and SMTP_APP_PASSWORD)


def _is_permanent_smtp_error(exc: Exception) -> bool:
    """5xx SMTP replies (bad address, auth rejected, etc.) won't succeed on retry."""
    code = getattr(exc, "smtp_code", None)
    return isinstance(exc, smtplib.SMTPResponseException) and code is not None and 500 <= code < 600


def send_email(to_email: str, subject: str, body: str, html_body: str | None = None) -> bool:
    """Sends a plain-text email, or a multipart text+HTML email if html_body is given.

    Mail clients that render HTML will show html_body; everything else falls
    back to the plain-text body (MIME requires the plain-text alternative to
    be attached first).

    Transient SMTP errors are retried up to SMTP_MAX_ATTEMPTS times with
    backoff; on final failure this logs the real exception and returns False
    rather than raising, so a flaky send never breaks the booking it's for.
    """
    if not is_configured():
        return False

    if html_body:
        msg: MIMEMultipart | MIMEText = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
    else:
        msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email

    for attempt in range(1, SMTP_MAX_ATTEMPTS + 1):
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_APP_PASSWORD)
                server.sendmail(SMTP_USER, [to_email], msg.as_string())
            return True
        except _TRANSIENT_SMTP_ERRORS as exc:
            if _is_permanent_smtp_error(exc) or attempt == SMTP_MAX_ATTEMPTS:
                logger.exception(
                    "send_email to %s failed permanently after %d attempt(s)", to_email, attempt
                )
                return False
            logger.warning(
                "send_email to %s failed on attempt %d/%d (%s: %s) — retrying in %ds",
                to_email, attempt, SMTP_MAX_ATTEMPTS, type(exc).__name__, exc,
                SMTP_RETRY_BACKOFF_SECONDS * attempt,
            )
            time.sleep(SMTP_RETRY_BACKOFF_SECONDS * attempt)
        except Exception:
            logger.exception("send_email to %s failed with a non-retryable error", to_email)
            return False
    return False
