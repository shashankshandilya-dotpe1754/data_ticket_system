"""
Google Sheets operations using plain `requests` calls to the Sheets REST
API. Token refresh happens once per request in auth.py — not here.
"""

import datetime
from urllib.parse import quote
from html.parser import HTMLParser
import html as html_module

import requests

import config

SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"


def _headers(creds):
    return {
        "Authorization": f"Bearer {creds.token}",
        "Accept": "application/json",
    }


def _values_url(range_str, suffix=""):
    return (
        f"{SHEETS_API_BASE}/{config.SPREADSHEET_ID}/values/"
        f"{quote(range_str, safe='')}{suffix}"
    )


def _row_from_ticket(ticket: dict) -> list:
    return [ticket.get(col, "") for col in config.COLUMNS]


def _ticket_from_row(row: list) -> dict:
    row = row + [""] * (len(config.COLUMNS) - len(row))
    return dict(zip(config.COLUMNS, row))


def get_all_tickets(creds) -> list[dict]:
    resp = requests.get(
        _values_url(config.SHEET_RANGE),
        headers=_headers(creds),
        timeout=20,
    )
    resp.raise_for_status()
    values = resp.json().get("values", [])
    if not values:
        return []

    tickets = []
    for i, row in enumerate(values[1:], start=2):
        if not row or row[0] == "":
            continue
        ticket = _ticket_from_row(row)
        ticket["_row"] = i
        tickets.append(ticket)
    return tickets


def get_ticket(creds, ticket_id: str) -> dict | None:
    for ticket in get_all_tickets(creds):
        if ticket["Ticket ID"] == ticket_id:
            return ticket
    return None


def next_ticket_id(creds) -> str:
    tickets = get_all_tickets(creds)
    year = datetime.datetime.now().year
    number = len(tickets) + 1
    existing = {t["Ticket ID"] for t in tickets}
    while True:
        ticket_id = f"TCK-{year}-{number:04d}"
        if ticket_id not in existing:
            return ticket_id
        number += 1


def append_ticket(creds, ticket: dict):
    row = _row_from_ticket(ticket)
    resp = requests.post(
        _values_url(config.SHEET_RANGE, ":append"),
        headers=_headers(creds),
        params={
            "valueInputOption": "USER_ENTERED",
            "insertDataOption": "INSERT_ROWS",
        },
        json={"values": [row]},
        timeout=20,
    )
    resp.raise_for_status()


def update_ticket_fields(creds, ticket_id: str, updates: dict, ticket: dict = None):
    if ticket is None:
        ticket = get_ticket(creds, ticket_id)
        if ticket is None:
            raise ValueError(f"{ticket_id} not found")

    row = ticket["_row"]

    for column, value in updates.items():
        index = config.COLUMNS.index(column)
        letter = chr(ord("A") + index)
        rng = f"{config.SHEET_NAME}!{letter}{row}"
        resp = requests.put(
            _values_url(rng),
            headers=_headers(creds),
            params={"valueInputOption": "USER_ENTERED"},
            json={"values": [[value]]},
            timeout=20,
        )
        resp.raise_for_status()

    ticket.update(updates)
    return ticket


class _StripHTML(HTMLParser):
    BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3",
                  "h4", "h5", "h6", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_plain_text(html_str: str) -> str:
    if not html_str:
        return ""
    parser = _StripHTML()
    parser.feed(html_str)
    text = html_module.unescape("".join(parser.parts))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines).strip()


def transfer_ticket(creds, old_ticket: dict, new_assignee: str,
                     transfer_by: str, transfer_reason: str, now_string: str) -> dict:
    """Returns the newly created ticket dict. The OLD ticket row is left
    completely untouched — it stays under its original assignee as a
    historical record."""

    new_ticket_id = next_ticket_id(creds)

    new_ticket = dict(old_ticket)
    new_ticket.pop("_row", None)

    new_ticket.update({
        "Ticket ID": new_ticket_id,
        "Created Date": now_string,
        "Status": "Open",
        "Assigned To": new_assignee,
        "Updated Date": now_string,
        "Closed Date": "",
        "Parent Ticket ID": old_ticket.get("Parent Ticket ID") or old_ticket["Ticket ID"],
        "Previous Ticket ID": old_ticket["Ticket ID"],
        "Transfer By": transfer_by,
        "Transfer Date": now_string,
        "Transfer Reason": transfer_reason,
        "Acceptor Description": "",
        "Thread Id": "",
        "RFC Message Id": "",
    })

    append_ticket(creds, new_ticket)
    return new_ticket


def get_acceptors(creds):

    resp = requests.get(
        _values_url("Acceptors!A:A"),
        headers=_headers(creds),
        timeout=20,
    )

    resp.raise_for_status()

    values = resp.json().get("values", [])

    acceptors = []

    for row in values:

        if row and row[0].strip():

            if row[0].lower() != "email":

                acceptors.append(row[0].strip().lower())

    return acceptors


def add_acceptor(creds, email):

    email = email.strip().lower()

    existing = get_acceptors(creds)

    if email in existing:
        return False

    resp = requests.post(

        _values_url("Acceptors!A:A", ":append"),

        headers=_headers(creds),

        params={
            "valueInputOption": "USER_ENTERED",
            "insertDataOption": "INSERT_ROWS",
        },

        json={
            "values": [
                [email]
            ]
        },

        timeout=20,
    )

    resp.raise_for_status()

    return True


def delete_acceptor(creds, email):

    email = email.strip().lower()

    resp = requests.get(
        _values_url("Acceptors!A:A"),
        headers=_headers(creds),
        timeout=20,
    )

    resp.raise_for_status()

    values = resp.json().get("values", [])

    row_number = None

    for idx, row in enumerate(values[1:], start=1):

        if row and row[0].strip().lower() == email:

            row_number = idx

            break

    if row_number is None:
        return False

    requests_body = {

        "requests": [

            {

                "deleteDimension": {

                    "range": {

                        "sheetId": config.ACCEPTOR_SHEET_ID,

                        "dimension": "ROWS",

                        "startIndex": row_number,

                        "endIndex": row_number + 1

                    }

                }

            }

        ]

    }

    resp = requests.post(

        f"{SHEETS_API_BASE}/{config.SPREADSHEET_ID}:batchUpdate",

        headers=_headers(creds),

        json=requests_body,

        timeout=20,

    )

    resp.raise_for_status()

    return True


# ==========================================================
# Conversation Functions
# ==========================================================

def _conversation_row(message: dict):
    return [
        message.get(col, "")
        for col in config.CONVERSATION_COLUMNS
    ]


def append_conversation_message(creds, message: dict):
    """
    Appends one message to Conversation sheet.
    """

    row = _conversation_row(message)

    resp = requests.post(

        _values_url(
            config.CONVERSATION_SHEET_RANGE,
            ":append",
        ),

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

    resp.raise_for_status()


def get_conversation(creds, ticket_id):
    """
    Returns all conversation messages for a ticket.
    """

    resp = requests.get(

        _values_url(
            config.CONVERSATION_SHEET_RANGE
        ),

        headers=_headers(creds),

        timeout=20,
    )

    resp.raise_for_status()

    values = resp.json().get("values", [])

    if not values:
        return []

    conversations = []

    for row in values[1:]:

        row = row + [""] * (
            len(config.CONVERSATION_COLUMNS) - len(row)
        )

        item = dict(
            zip(
                config.CONVERSATION_COLUMNS,
                row,
            )
        )

        if item["Ticket ID"] == ticket_id:
            conversations.append(item)

    conversations.sort(
        key=lambda x: x["Message Time"]
    )

    return conversations


def get_latest_conversation(creds, ticket_id):
    """
    Returns latest message or None.
    """

    conversations = get_conversation(
        creds,
        ticket_id,
    )

    if not conversations:
        return None

    return conversations[-1]


def delete_conversation(creds, ticket_id):
    """
    Reserved for future use.

    We generally don't delete conversations,
    but this helper exists for future cleanup.
    """

    pass
