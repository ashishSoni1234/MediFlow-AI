"""Email sending via SMTP (Gmail App Password), per Section 1's "simpler fallback".

If SMTP credentials aren't configured, send_email returns False rather than
raising, so the send_appointment_confirmation_email tool can report the
outcome to the LLM without crashing the request.
"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD")


def is_configured() -> bool:
    return bool(SMTP_USER and SMTP_APP_PASSWORD)


def send_email(to_email: str, subject: str, body: str) -> bool:
    if not is_configured():
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_USER, [to_email], msg.as_string())
    return True
