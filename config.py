"""
Central configuration for the Data Team Ticket System.
"""

import os
import json
from datetime import timedelta
from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")
# ==========================================================
# BASE DIRECTORY
# ==========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Allow OAuth over localhost during development only
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# ==========================================================
# GOOGLE OAUTH
# ==========================================================

CLIENT_SECRET_JSON = os.environ.get("CLIENT_SECRET_JSON")

if CLIENT_SECRET_JSON:
    CLIENT_CONFIG = json.loads(CLIENT_SECRET_JSON)
else:
    # Local development fallback: read the file directly if the env
    # var isn't set (e.g. running on your own machine, not on Render).
    local_secret_path = os.path.join(BASE_DIR, "client_secret.json")
    if os.path.exists(local_secret_path):
        with open(local_secret_path) as f:
            CLIENT_CONFIG = json.load(f)
    else:
        CLIENT_CONFIG = None

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

OAUTH_REDIRECT_URI = os.environ.get(
    "OAUTH_REDIRECT_URI",
    "http://localhost:5000/oauth2callback",
)

# ==========================================================
# GOOGLE SHEETS
# ==========================================================

SPREADSHEET_ID = "1eRoIFbmEqZ40oKVKwb2vuE1VUyuUDWIr3sRnKT9mSt0"

SHEET_NAME = "Tickets"

SHEET_RANGE = f"{SHEET_NAME}!A:T"

COLUMNS = [
    "Ticket ID",
    "Created Date",
    "Requestor Email",
    "Subject",
    "Requestor Description",
    "Priority",
    "High Priority Reason",
    "Status",
    "Assigned To",
    "Attachments",
    "Updated Date",
    "Closed Date",

    "Parent Ticket ID",
    "Previous Ticket ID",
    "Transfer By",
    "Transfer Date",
    "Transfer Reason",

    "Acceptor Description",
    "Thread Id",
    "RFC Message Id",
]

# ==========================================================
# TEAM MEMBERS
# ==========================================================

ACCEPTORS = [
    "pradeep.singh1@dotpe.in",
    "shashank.shandilya@dotpe.in",
    "sahil.kaku@dotpe.in",
]

RECEIVERS = [
    "pradeep.singh1@dotpe.in",
    "shashank.shandilya@dotpe.in",
    "sahil.kaku@dotpe.in",
]

DEFAULT_CC_RULES = {
    "shashank.shandilya@dotpe.in": [
        "pradeep.singh1@dotpe.in"
    ],
    "sahil.kaku@dotpe.in": [
        "pradeep.singh1@dotpe.in"
    ],
    "pradeep.singh1@dotpe.in": [],
}


def default_cc_for_assignee(assignee_email):
    if not assignee_email:
        return []

    return DEFAULT_CC_RULES.get(assignee_email, [])


STATUS_OPTIONS = [
    "Open",
    "In Progress",
    "On Hold",
    "Resolved",
    "Closed",
]

PRIORITY_OPTIONS = [
    "Low",
    "Medium",
    "High",
]

# ==========================================================
# OFFICE HOURS
# ==========================================================

OFFICE_HOURS_START = 10
OFFICE_HOURS_END = 19

AVAILABILITY_OPTIONS = [
    "Available",
    "Today - On Leave",
    "Tomorrow - On Leave",
    "Tomorrow - Holiday",
]


def is_acceptor_email(email):
    return email.lower() in [
        x.lower()
        for x in ACCEPTORS
    ]


# ==========================================================
# FLASK
# ==========================================================

SECRET_KEY = os.environ.get(
    "FLASK_SECRET_KEY",
    "change-this-to-a-random-secret"
)

PERMANENT_SESSION_LIFETIME = timedelta(days=30)

UPLOAD_FOLDER = os.path.join(
    BASE_DIR,
    "uploads"
)

MAX_CONTENT_LENGTH = 25 * 1024 * 1024
