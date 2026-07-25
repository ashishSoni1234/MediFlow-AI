"""One-time helper to mint a Google Calendar OAuth refresh token.

Run locally, then paste the printed GOOGLE_REFRESH_TOKEN into .env:

    python scripts/get_google_refresh_token.py

This opens your browser for the Google consent screen and starts a local
loopback server to receive the redirect (standard installed-app flow —
matches the "Ashish" Desktop client type in Google Cloud Console).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def main() -> None:
    client_config = {
        "installed": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    print("\n--- Success. Update these in your .env ---")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
