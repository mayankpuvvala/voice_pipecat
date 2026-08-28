"""One-time interactive setup for app/services/drive_oauth_client.py.

Run with `python -m app.services.drive_oauth_setup`. Opens your default
browser to Google's consent screen; after you approve, prints a refresh
token to paste into .env (locally) and Railway's variables (deployed) as
GOOGLE_OAUTH_REFRESH_TOKEN. Requires GOOGLE_OAUTH_CLIENT_ID and
GOOGLE_OAUTH_CLIENT_SECRET to already be set — see drive_oauth_client.py's
docstring for how to create those.

Only needs to be run once. The refresh token doesn't expire under normal
use (only if explicitly revoked, unused for 6 months, or the OAuth consent
screen is still in "Testing" mode and the 7-day testing-token limit applies
— if uploads start failing with an auth error after a week, that's the
likely cause: publish the OAuth consent screen, or re-run this).
"""

from __future__ import annotations

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from app.config.settings import settings
from app.services.drive_oauth_client import SCOPES


def main() -> None:
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        print(
            "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET not set in .env — "
            "create an OAuth Client ID (Desktop app) first, see "
            "app/services/drive_oauth_client.py's docstring.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    client_config = {
        "installed": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    print("\nConsent granted. Set this in .env and Railway's variables:\n")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
