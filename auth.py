"""
Handles "Login with Gmail" for both requestors and acceptors.

Each logged-in user's OAuth token is kept server-side in a small per-user
JSON file under .tokens/ (keyed by their email) and referenced from the
Flask session by email only. This means:
  - Every email the system sends is sent using THAT user's own Gmail
    account (so it carries their real signature, "from" address, etc).
  - No shared/service credentials are used for sending mail.

NOTE: for a production deployment, swap the flat-file token store for an
encrypted database table (see README.md "Productionizing" section).
"""

"""
Handles "Login with Gmail" for both requestors and acceptors.
"""


"""
Google OAuth helper functions.
Uses OAuth client JSON from Render Environment Variable:
CLIENT_SECRET_JSON
"""

import requests
from authlib.integrations.flask_client import OAuth
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

import config

oauth = OAuth()


def init_oauth(app):
    oauth.init_app(app)

    oauth.register(
        name="google",
        client_id=config.CLIENT_CONFIG["web"]["client_id"],
        client_secret=config.CLIENT_CONFIG["web"]["client_secret"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": " ".join(config.SCOPES)
        },
    )


def google():
    return oauth.google


def credentials_from_session(session):

    if "credentials" not in session:
        return None

    creds = Credentials(**session["credentials"])

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        session["credentials"] = credentials_to_dict(creds)

    return creds


def credentials_to_dict(creds):

    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def get_user_email(access_token):

    response = requests.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
    )

    response.raise_for_status()

    return response.json()["email"]

def is_acceptor(email):
    return config.is_acceptor_email(email)
