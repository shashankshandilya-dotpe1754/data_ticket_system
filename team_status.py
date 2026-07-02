"""
Two small pieces of "team state" that don't belong on the ticket sheet
itself:

1. Registered acceptors — every Gmail account that has logged in AND is
   listed in config.ACCEPTORS gets recorded here. The "Assign / Transfer
   To" dropdown is built from this list (not the static config list),
   because only someone who has actually completed Gmail OAuth can have
   mail sent "as" them.

2. Availability notice — any acceptor can set a shared, team-wide status
   (e.g. "Tomorrow is a holiday") that gets shown to requestors raising
   tickets outside office hours, and included in their confirmation email.
"""

import os
import json
import datetime

import config

REGISTRY_FILE = os.path.join(os.path.dirname(__file__), ".acceptors_registry.json")
AVAILABILITY_FILE = os.path.join(os.path.dirname(__file__), ".availability_store.json")


# ---------------------------------------------------------------------------
# Registered acceptors (requirement 17)
# ---------------------------------------------------------------------------
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


def get_assignable_acceptors() -> list:
    """Acceptors who can actually be assigned tickets right now — i.e.
    have logged in at least once. Falls back to the static config list
    only if nobody has logged in yet, so the dropdown is never empty on
    day one."""
    registered = get_registered_acceptors()
    return registered if registered else list(config.ACCEPTORS)


# ---------------------------------------------------------------------------
# Availability / OOO notice (requirement 16)
# ---------------------------------------------------------------------------
DEFAULT_AVAILABILITY = {
    "status": "Available",
    "note": "",
    "set_by": "",
    "updated": "",
}


def is_outside_office_hours(dt: datetime.datetime = None) -> bool:
    dt = dt or datetime.datetime.now()
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
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(AVAILABILITY_FILE, "w") as f:
        json.dump(data, f)
    return data


def availability_banner(dt: datetime.datetime = None) -> str | None:
    """Returns a human-readable banner string if a ticket raised right now
    would land outside office hours and/or the team has an active
    leave/holiday notice set. Returns None if everything is normal."""
    dt = dt or datetime.datetime.now()
    avail = get_availability()
    outside_hours = is_outside_office_hours(dt)
    status = avail.get("status", "Available")

    if status == "Available" and not outside_hours:
        return None

    parts = []
    if outside_hours:
        parts.append(
            f"This ticket is being raised outside office hours "
            f"({config.OFFICE_HOURS_START}:00–{config.OFFICE_HOURS_END}:00)."
        )
    if status != "Available":
        parts.append(f"Team status: {status}.")
    if avail.get("note"):
        parts.append(avail["note"])
    parts.append("It will be picked up on the next working day.")
    return " ".join(parts)
