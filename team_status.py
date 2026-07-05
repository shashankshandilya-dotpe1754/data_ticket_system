"""
Two small pieces of "team state" that don't belong on the ticket sheet
itself: registered acceptors, and the team-wide availability/OOO notice.
"""

import os
import json
import datetime
import auth
import config
import sheets_utils

REGISTRY_FILE = os.path.join(os.path.dirname(__file__), ".acceptors_registry.json")
AVAILABILITY_FILE = os.path.join(os.path.dirname(__file__), ".availability_store.json")


def register_acceptor_login(email: str):
    if not config.is_acceptor_email(email):
        return
    emails = get_registered_acceptors()
    if email not in emails:
        emails.append(email)
        with open(REGISTRY_FILE, "w") as f:
            json.dump(emails, f)


def get_registered_acceptors() -> list:
    if not os.path.exists(REGISTRY_FILE):
        return []
    with open(REGISTRY_FILE) as f:
        return json.load(f)


import sheets_utils
import auth


def get_assignable_acceptors(creds=None) -> list:
    """
    Always read the latest acceptors from Google Sheet.
    """

    if creds:
        try:
            return sheets_utils.get_acceptors(creds)
        except Exception:
            pass

    registered = get_registered_acceptors()

    if registered:
        return registered

    return list(config.ACCEPTORS)


DEFAULT_AVAILABILITY = {
    "status": "Available",
    "note": "",
    "set_by": "",
    "updated": "",
}


def is_outside_office_hours(dt: datetime.datetime = None) -> bool:
    dt = dt or datetime.datetime.now(config.IST)
    return not (config.OFFICE_HOURS_START <= dt.hour < config.OFFICE_HOURS_END)


def get_availability() -> dict:
    if not os.path.exists(AVAILABILITY_FILE):
        return dict(DEFAULT_AVAILABILITY)
    with open(AVAILABILITY_FILE) as f:
        return json.load(f)


def set_availability(status: str, note: str, set_by: str):
    data = {
        "status": status,
        "note": note.strip(),
        "set_by": set_by,
        "updated": datetime.datetime.now(config.IST).strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(AVAILABILITY_FILE, "w") as f:
        json.dump(data, f)
    return data


def availability_banner(dt: datetime.datetime = None) -> str | None:
    dt = dt or datetime.datetime.now(config.IST)
    avail = get_availability()
    outside_hours = is_outside_office_hours(dt)
    status = avail.get("status", "Available")

    if status == "Available" and not outside_hours:
        return None

    parts = []
    if outside_hours:
        parts.append(
            f"This ticket is being raised outside office hours "
            f"({config.OFFICE_HOURS_START}:00–{config.OFFICE_HOURS_END}:00 IST)."
        )
    if status != "Available":
        parts.append(f"Team status: {status}.")
    if avail.get("note"):
        parts.append(avail["note"])
    parts.append("It will be picked up on the next working day.")
    return " ".join(parts)
