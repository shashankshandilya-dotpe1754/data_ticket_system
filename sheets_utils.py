"""
Reads/writes the shared Google Sheet which acts as the system of record.

MEMORY NOTE: the old version of this file called build("sheets", "v4", ...)
inside almost every function, and update_ticket_fields() internally called
get_ticket() -> get_all_tickets() AGAIN even when the caller had already
just fetched that exact ticket a moment earlier. A single "change status"
request was building the Sheets client 2-3 times and reading the entire
sheet 2-3 times.

Fix: every function now takes an already-built `service` object (built
ONCE per request via sheets_service(creds)) instead of raw creds, and
update_ticket_fields() accepts an already-fetched `ticket` dict so it
doesn't need to re-read the whole sheet to find the row number again.
"""

import datetime
from googleapiclient.discovery import build

import config


def sheets_service(creds):
    """Call this ONCE per request, then pass the returned object into
    every other function below instead of calling this repeatedly."""
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _row_from_ticket(t: dict) -> list:
    return [t.get(col, "") for col in config.COLUMNS]


def _ticket_from_row(row: list) -> dict:
    row = row + [""] * (len(config.COLUMNS) - len(row))
    return dict(zip(config.COLUMNS, row))


def get_all_tickets(service) -> list[dict]:
    result = service.spreadsheets().values().get(
        spreadsheetId=config.SPREADSHEET_ID, range=config.SHEET_RANGE
    ).execute()
    values = result.get("values", [])
    if not values:
        return []
    rows = values[1:]  # skip header
    tickets = []
    for i, row in enumerate(rows, start=2):  # sheet row number
        if not row or not row[0]:
            continue
        t = _ticket_from_row(row)
        t["_row"] = i
        tickets.append(t)
    return tickets


def get_ticket(service, ticket_id: str) -> dict | None:
    for t in get_all_tickets(service):
        if t.get("Ticket ID") == ticket_id:
            return t
    return None


def next_ticket_id(service) -> str:
    tickets = get_all_tickets(service)
    year = datetime.datetime.now().year
    n = len(tickets) + 1
    candidate = f"TCK-{year}-{n:04d}"
    existing_ids = {t["Ticket ID"] for t in tickets}
    while candidate in existing_ids:
        n += 1
        candidate = f"TCK-{year}-{n:04d}"
    return candidate


def append_ticket(service, ticket: dict):
    row = _row_from_ticket(ticket)
    service.spreadsheets().values().append(
        spreadsheetId=config.SPREADSHEET_ID,
        range=config.SHEET_RANGE,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def update_ticket_fields(service, ticket_id: str, updates: dict, ticket: dict = None):
    """Update only the given column(s) for a ticket, in place.

    If `ticket` (an already-fetched dict with a "_row" key) is passed in,
    this skips re-reading the whole sheet to find the row — the caller
    almost always already has it from a prior get_ticket() call in the
    same request.
    """
    if ticket is None:
        ticket = get_ticket(service, ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found in sheet")

    row_num = ticket["_row"]

    for col_name, value in updates.items():
        col_index = config.COLUMNS.index(col_name)  # 0-based
        col_letter = chr(ord("A") + col_index)
        service.spreadsheets().values().update(
            spreadsheetId=config.SPREADSHEET_ID,
            range=f"{config.SHEET_NAME}!{col_letter}{row_num}",
            valueInputOption="USER_ENTERED",
            body={"values": [[value]]},
        ).execute()

    ticket.update(updates)
    return ticket
