"""
Handles "Login with Gmail" for both requestors and acceptors, on Render.

Credentials are stored as a plain dict in the Flask session (signed
cookie). The dict MUST contain every field google-auth needs to refresh
an expired access token on its own:
    token, refresh_token, token_uri, client_id, client_secret, scopes
"""

import requests
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

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
    }


from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


def credentials_from_dict(data):

    if not data:
        return None

    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )

    if creds.expired and creds.refresh_token:

        creds.refresh(Request())

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
