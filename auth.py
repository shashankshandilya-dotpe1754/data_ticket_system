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

import os
import json
import auth
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

import config

TOKENS_DIR = os.path.join(os.path.dirname(__file__), ".tokens")
os.makedirs(TOKENS_DIR, exist_ok=True)


def _token_path(email: str):
    safe = email.replace("/", "_").replace("\\", "_")
    return os.path.join(TOKENS_DIR, f"{safe}.json")


# --------------------------------------------------------
# OAuth Flow
# --------------------------------------------------------
from google_auth_oauthlib.flow import Flow
def build_flow(state=None):

    flow = Flow.from_client_secrets_file(
    config.CLIENT_SECRETS_FILE,
    scopes=config.SCOPES,
    state=state
)


    flow.redirect_uri = config.OAUTH_REDIRECT_URI

    return flow


# --------------------------------------------------------
# Save Token
# --------------------------------------------------------

def save_credentials(email: str, creds: Credentials):

    with open(_token_path(email), "w") as f:
        f.write(creds.to_json())


# --------------------------------------------------------
# Load Token
# --------------------------------------------------------

def load_credentials(email: str):

    path = _token_path(email)

    if not os.path.exists(path):
        return None

    with open(path, "r") as f:
        data = json.load(f)

    creds = Credentials.from_authorized_user_info(
        data,
        scopes=config.SCOPES,
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(email, creds)
        except Exception:
            return None

    return creds


# --------------------------------------------------------
# Gmail Address
# --------------------------------------------------------

def get_user_email(creds):

    service = build(
        "oauth2",
        "v2",
        credentials=creds,
    )

    profile = service.userinfo().get().execute()

    return profile["email"]


# --------------------------------------------------------
# Acceptor Check
# --------------------------------------------------------

def is_acceptor(email):

    return email.lower() in [
        x.lower()
        for x in config.ACCEPTORS
    ]


# --------------------------------------------------------
# Delete Token
# --------------------------------------------------------

def delete_credentials(email):

    path = _token_path(email)

    if os.path.exists(path):
        os.remove(path)