"""One-time interactive setup for drive_oauth_client.py.

Run with `python -m app.services.drive_oauth_setup`. Opens the consent
screen and prints a refresh token to set as GOOGLE_OAUTH_REFRESH_TOKEN.
Requires GOOGLE_OAUTH_CLIENT_ID/SECRET already set.

Set RESTAURANT_ID before running to onboard another restaurant — the
printed variable name comes out suffixed (see settings.py's
_restaurant_env). If uploads fail after a week, the OAuth consent screen
is probably still in "Testing" mode — publish it, or re-run this.
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

    var_name = f"GOOGLE_OAUTH_REFRESH_TOKEN_{settings.restaurant_id}"
    print("\nConsent granted. Set this in .env and Railway's variables:\n")
    print(f"{var_name}={creds.refresh_token}")


if __name__ == "__main__":
    main()
