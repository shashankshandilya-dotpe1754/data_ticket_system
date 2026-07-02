"""
Central configuration for the Data Team Ticket System.
Edit the values in this file for your environment.
"""

import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CLIENT_SECRETS_FILE = os.path.join(
    BASE_DIR,
    "client_secret.json"
)
# Allow OAuth over localhost during development
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# ---------------------------------------------------------------------------
# GOOGLE SHEET (your existing "Data Request Portal" sheet)
# ---------------------------------------------------------------------------
# Taken from your sheet URL:
# https://docs.google.com/spreadsheets/d/1eRoIFbmEqZ40oKVKwb2vuE1VUyuUDWIr3sRnKT9mSt0/edit?gid=0
SPREADSHEET_ID = "1eRoIFbmEqZ40oKVKwb2vuE1VUyuUDWIr3sRnKT9mSt0"
SHEET_NAME = "Tickets"          # the tab name at the bottom of your sheet
SHEET_RANGE = f"{SHEET_NAME}!A:M"

# Column order MUST match your sheet header row exactly:
# A: Ticket ID | B: Created Date | C: Requestor Email | D: Subject
# E: Requestor Description | F: Priority | G: High Priority Reason
# H: Status | I: Assigned To | J: Attachment | K: Updated Date
# L: Closed Date | M: Acceptor Description
COLUMNS = [
    "Ticket ID", "Created Date", "Requestor Email", "Subject",
    "Requestor Description", "Priority", "High Priority Reason",
    "Status", "Assigned To", "Attachment", "Updated Date",
    "Closed Date", "Acceptor Description",
]

# ---------------------------------------------------------------------------
# OAUTH / GOOGLE APIS
# ---------------------------------------------------------------------------
# Download this from Google Cloud Console -> APIs & Services -> Credentials
# (OAuth 2.0 Client ID, type "Web application"). See README.md for full steps.
CLIENT_SECRETS_FILE = os.path.join(os.path.dirname(__file__), "client_secret.json")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

# Must match a Redirect URI registered in Google Cloud Console exactly.
OAUTH_REDIRECT_URI = os.environ.get(
    "OAUTH_REDIRECT_URI", "http://localhost:5000/oauth2callback"
)

# ---------------------------------------------------------------------------
# TEAM / ROLES
# ---------------------------------------------------------------------------
# Anyone whose Gmail matches one of these is treated as an "Acceptor"
# (Data Team member who can see the dashboard, take, transfer, and close
# tickets). Everyone else who logs in is a "Requestor".
ACCEPTORS = [
    "pradeep.singh1@dotpe.in",
    "shashank.shandilya@dotpe.in",
    "sahil.kaku@dotpe.in",
]

# The ticket is initially addressed to ALL receivers (they triage/assign
# among themselves afterward via the dashboard).
RECEIVERS = [
    "pradeep.singh1@dotpe.in",
    "shashank.shandilya@dotpe.in",
    "sahil.kaku@dotpe.in",
]

# Default CC rules applied automatically based on who a ticket is
# assigned to (requirement: keep pradeep.singh1 in the loop unless he is
# himself the assignee).
DEFAULT_CC_RULES = {
    "shashank.shandilya@dotpe.in": ["pradeep.singh1@dotpe.in"],
    "sahil.kaku@dotpe.in": ["pradeep.singh1@dotpe.in"],
    "pradeep.singh1@dotpe.in": [],
}


def default_cc_for_assignee(assignee_email: str) -> list:
    if not assignee_email:
        return []
    return DEFAULT_CC_RULES.get(assignee_email, [])

STATUS_OPTIONS = ["Open", "In Progress", "On Hold", "Resolved", "Closed"]
PRIORITY_OPTIONS = ["Low", "Medium", "High"]

# ---------------------------------------------------------------------------
# OFFICE HOURS / AVAILABILITY (requirement 16)
# ---------------------------------------------------------------------------
OFFICE_HOURS_START = 10   # 10 AM, 24-hour clock, server local time
OFFICE_HOURS_END = 19     # 7 PM

AVAILABILITY_OPTIONS = [
    "Available",
    "Today - On Leave",
    "Tomorrow - On Leave",
    "Tomorrow - Holiday",
]


def is_acceptor_email(email: str) -> bool:
    return email.lower() in [a.lower() for a in ACCEPTORS]

# ---------------------------------------------------------------------------
# FLASK
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "change-this-to-a-random-secret")
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # 25 MB, Gmail's own attachment cap
