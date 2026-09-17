#!/usr/bin/env python3
"""One-off helper: mint a Gmail refresh token for the sending account.

Run it on your own machine (not on Railway) while signed into the Google
account the report should be sent *from*:

    pip install google-auth-oauthlib
    GMAIL_CLIENT_ID=... GMAIL_CLIENT_SECRET=... python scripts/gmail_oauth.py

It opens a browser, asks you to approve the "send email" permission, and
prints the GMAIL_REFRESH_TOKEN to paste into Railway's variables.

Getting the client id/secret first:
  1. https://console.cloud.google.com/ -> create (or pick) a project
  2. APIs & Services -> Library -> enable "Gmail API"
  3. APIs & Services -> OAuth consent screen -> External -> add yourself as a
     Test user (no verification needed while it stays in Testing)
  4. Credentials -> Create credentials -> OAuth client ID -> Desktop app
"""
from __future__ import annotations

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main() -> int:
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET first.", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        SCOPES,
    )
    # access_type=offline + prompt=consent guarantees a refresh token comes back
    # even if this account has authorised the app before.
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not credentials.refresh_token:
        print("No refresh token returned - revoke the app at "
              "https://myaccount.google.com/permissions and try again.", file=sys.stderr)
        return 1

    print("\nAdd these to Railway:\n")
    print(f"GMAIL_CLIENT_ID={client_id}")
    print(f"GMAIL_CLIENT_SECRET={client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={credentials.refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
