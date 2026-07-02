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

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import requests
import config


def build_flow(state=None):

    print("=" * 80)
    print("CLIENT CONFIG")
    print(config.CLIENT_CONFIG)
    print("=" * 80)

    flow = Flow.from_client_config(
        config.CLIENT_CONFIG,
        scopes=config.SCOPES,
        state=state,
    )

    flow.redirect_uri = config.OAUTH_REDIRECT_URI

    return flow


def credentials_from_session(session):
    """
    Rebuild Google Credentials from Flask session.
    """

    if "credentials" not in session:
        return None

    creds = Credentials(**session["credentials"])

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        session["credentials"] = credentials_to_dict(creds)

    return creds


def credentials_to_dict(creds):
    """
    Convert Credentials object into session dictionary.
    """

    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def get_user_email(credentials):
    """
    Return authenticated Gmail address.
    """

    response = requests.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={
            "Authorization": f"Bearer {credentials.token}"
        },
    )

    response.raise_for_status()

    return response.json()["email"]
