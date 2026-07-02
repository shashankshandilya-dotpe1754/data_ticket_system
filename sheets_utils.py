"""
Reads/writes the shared Google Sheet (your "Data Request Portal" sheet)
which acts as the system of record. Any authenticated user's credentials
can be used here (both requestor and acceptor tokens have the
spreadsheets scope) — we use the acceptor's or requestor's own token so
that Sheet edit history correctly attributes who changed what.
"""

import datetime
from googleapiclient.discovery import build

import config


def sheets_service(creds):
    return build("sheets", "v4", credentials=creds)


def _row_from_ticket(t: dict) -> list:
    return [t.get(col, "") for col in config.COLUMNS]


def _ticket_from_row(row: list) -> dict:
    row = row + [""] * (len(config.COLUMNS) - len(row))
    return dict(zip(config.COLUMNS, row))


def get_all_tickets(creds) -> list[dict]:
    service = sheets_service(creds)
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


def get_ticket(creds, ticket_id: str) -> dict | None:
    for t in get_all_tickets(creds):
        if t.get("Ticket ID") == ticket_id:
            return t
    return None


def next_ticket_id(creds) -> str:
    tickets = get_all_tickets(creds)
    year = datetime.datetime.now().year
    n = len(tickets) + 1
    candidate = f"TCK-{year}-{n:04d}"
    existing_ids = {t["Ticket ID"] for t in tickets}
    while candidate in existing_ids:
        n += 1
        candidate = f"TCK-{year}-{n:04d}"
    return candidate


def append_ticket(creds, ticket: dict):
    service = sheets_service(creds)
    row = _row_from_ticket(ticket)
    service.spreadsheets().values().append(
        spreadsheetId=config.SPREADSHEET_ID,
        range=config.SHEET_RANGE,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def update_ticket_fields(creds, ticket_id: str, updates: dict):
    """Update only the given column(s) for a ticket, in place, by
    locating its row first."""
    ticket = get_ticket(creds, ticket_id)
    if not ticket:
        raise ValueError(f"Ticket {ticket_id} not found in sheet")
    row_num = ticket["_row"]
    service = sheets_service(creds)

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
