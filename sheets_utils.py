"""
Google Sheets operations using plain requests.
Memory optimized version with debugging.
"""

import datetime
from urllib.parse import quote
from html.parser import HTMLParser
import html as html_module

import requests
from google.auth.transport.requests import Request

import config


SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"


# ==========================================================
# AUTH HEADER
# ==========================================================

def _headers(creds):

    try:
        if creds.expired:
            creds.refresh(Request())
    except Exception as e:
        print("REFRESH ERROR:", e)

    print("=" * 80)
    print("TOKEN:", creds.token[:40])
    print("=" * 80)

    return {
        "Authorization": f"Bearer {creds.token}",
        "Accept": "application/json",
    }


# ==========================================================
# URL
# ==========================================================

def _values_url(range_str, suffix=""):

    return (
        f"{SHEETS_API_BASE}/"
        f"{config.SPREADSHEET_ID}"
        f"/values/"
        f"{quote(range_str, safe='')}"
        f"{suffix}"
    )


# ==========================================================
# ROW <-> DICT
# ==========================================================

def _row_from_ticket(ticket):

    return [
        ticket.get(col, "")
        for col in config.COLUMNS
    ]


def _ticket_from_row(row):

    row = row + [""] * (len(config.COLUMNS) - len(row))

    return dict(
        zip(
            config.COLUMNS,
            row
        )
    )


# ==========================================================
# GET ALL TICKETS
# ==========================================================

def get_all_tickets(creds):

    headers = _headers(creds)
    print(headers)
    
    resp = requests.get(
        _values_url(config.SHEET_RANGE),
        headers=headers,
        timeout=20,
    )

    print("=" * 80)
    print("GOOGLE SHEETS DEBUG")
    print("URL :", resp.url)
    print("STATUS :", resp.status_code)
    print(resp.text)
    print("=" * 80)

    resp.raise_for_status()

    values = resp.json().get("values", [])

    if not values:
        return []

    tickets = []

    for i, row in enumerate(values[1:], start=2):

        if not row:
            continue

        if len(row) == 0:
            continue

        if row[0] == "":
            continue

        ticket = _ticket_from_row(row)

        ticket["_row"] = i

        tickets.append(ticket)

    return tickets


# ==========================================================
# GET ONE TICKET
# ==========================================================

def get_ticket(creds, ticket_id):

    tickets = get_all_tickets(creds)

    for ticket in tickets:

        if ticket["Ticket ID"] == ticket_id:
            return ticket

    return None


# ==========================================================
# NEXT TICKET ID
# ==========================================================

def next_ticket_id(creds):

    tickets = get_all_tickets(creds)

    year = datetime.datetime.now().year

    number = len(tickets) + 1

    existing = {
        x["Ticket ID"]
        for x in tickets
    }

    while True:

        ticket_id = f"TCK-{year}-{number:04d}"

        if ticket_id not in existing:
            return ticket_id

        number += 1


# ==========================================================
# APPEND
# ==========================================================

def append_ticket(creds, ticket):

    row = _row_from_ticket(ticket)

    resp = requests.post(
        _values_url(config.SHEET_RANGE, ":append"),
        headers=_headers(creds),
        params={
            "valueInputOption": "USER_ENTERED",
            "insertDataOption": "INSERT_ROWS",
        },
        json={
            "values": [row]
        },
        timeout=20,
    )

    print(resp.text)

    resp.raise_for_status()


# ==========================================================
# UPDATE
# ==========================================================

def update_ticket_fields(
    creds,
    ticket_id,
    updates,
    ticket=None,
):

    if ticket is None:

        ticket = get_ticket(
            creds,
            ticket_id,
        )

    if ticket is None:

        raise ValueError(
            f"{ticket_id} not found"
        )

    row = ticket["_row"]

    for column, value in updates.items():

        index = config.COLUMNS.index(column)

        letter = chr(ord("A") + index)

        rng = f"{config.SHEET_NAME}!{letter}{row}"

        resp = requests.put(
            _values_url(rng),
            headers=_headers(creds),
            params={
                "valueInputOption": "USER_ENTERED"
            },
            json={
                "values": [[value]]
            },
            timeout=20,
        )

        print(resp.text)

        resp.raise_for_status()

    ticket.update(updates)

    return ticket


# ==========================================================
# HTML -> TEXT
# ==========================================================

class StripHTML(HTMLParser):

    def __init__(self):

        super().__init__()

        self.text = []

    def handle_data(self, data):

        self.text.append(data)


def html_to_plain_text(html):

    if not html:
        return ""

    parser = StripHTML()

    parser.feed(html)

    text = "".join(parser.text)

    return html_module.unescape(text).strip()


# ==========================================================
# Transfer
# ==========================================================
def transfer_ticket(
    creds,
    ticket_id,
    new_assignee,
    transfer_by,
    transfer_reason,
):
    """
    Creates a new transferred ticket.

    Phase 1 implementation.
    """

    return False, "Transfer logic not implemented yet."
