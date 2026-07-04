"""
Handles "Login with Gmail" for both requestors and acceptors, on Render.

Two real bugs fixed here:
1. `expiry` was never stored/restored, so Credentials.expired always
   evaluated False (google-auth's default when no expiry is set) — a
   stale ~1hr-old access token would never get refreshed.
2. If Google has actually revoked/expired the refresh token itself
   (invalid_grant — commonly happens when the OAuth consent screen is
   still in "Testing" mode, which caps refresh tokens at 7 days), the
   old code let that exception bubble up as an unhandled 500. Now it's
   caught and treated as "not logged in", so the person is sent back to
   /login instead of seeing a crash.
"""

import requests
from datetime import datetime
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

import config


def build_flow(state=None):
    if not config.CLIENT_CONFIG:
        raise RuntimeError(
            "CLIENT_SECRET_JSON environment variable is not set or "
            "could not be parsed as JSON. Check it in Render's "
            "Environment tab."
        )
    flow = Flow.from_client_config(
        config.CLIENT_CONFIG,
        scopes=config.SCOPES,
        state=state,
    )
    flow.redirect_uri = config.OAUTH_REDIRECT_URI
    return flow


def credentials_to_dict(creds: Credentials) -> dict:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }


def credentials_from_dict(data: dict):
    if not data or not data.get("refresh_token"):
        return None

    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )

    expiry_str = data.get("expiry")
    if expiry_str:
        creds.expiry = datetime.fromisoformat(expiry_str)

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # Refresh token itself is dead (revoked, expired, or the
            # OAuth client changed). Treat this as "not logged in"
            # rather than crashing — current_user() will clear the
            # session and send the person back to /login.
            return None

    return creds


def get_user_email(creds: Credentials) -> str:
    resp = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["email"]


def is_acceptor(email: str) -> bool:
    return config.is_acceptor_email(email)
