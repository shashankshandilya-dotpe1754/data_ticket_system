"""
Google Sheets operations using plain `requests` calls to the Sheets REST
API, instead of google-api-python-client (see gmail_utils.py for why —
same memory rationale applies here).
"""

import datetime
from urllib.parse import quote
import requests

import config

SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"


from google.auth.transport.requests import Request

def _headers(creds):

    try:
        creds.refresh(Request())
    except Exception:
        pass

    return {
        "Authorization": f"Bearer {creds.token}",
        "Accept": "application/json",
    }


def _values_url(range_str: str, suffix: str = "") -> str:
    return (
        f"{SHEETS_API_BASE}/{config.SPREADSHEET_ID}/values/"
        f"{quote(range_str, safe='')}{suffix}"
    )


def _row_from_ticket(t: dict) -> list:
    return [t.get(col, "") for col in config.COLUMNS]


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
    rows = values[1:]  # skip header
    tickets = []
    for i, row in enumerate(rows, start=2):
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
    year = datetime.datetime.now(config.IST).year
    n = len(tickets) + 1
    candidate = f"TCK-{year}-{n:04d}"
    existing_ids = {t["Ticket ID"] for t in tickets}
    while candidate in existing_ids:
        n += 1
        candidate = f"TCK-{year}-{n:04d}"
    return candidate


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
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found in sheet")

    row_num = ticket["_row"]

    for col_name, value in updates.items():
        col_index = config.COLUMNS.index(col_name)
        col_letter = chr(ord("A") + col_index)
        rng = f"{config.SHEET_NAME}!{col_letter}{row_num}"
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


def html_to_plain_text(html_str: str) -> str:
    """Strips HTML (from the Quill editors) down to readable plain text
    for storage in the Sheet, while the raw HTML is still used for the
    actual emails. Stdlib-only (no BeautifulSoup) to keep memory low."""
    if not html_str:
        return ""
    from html.parser import HTMLParser
    import html as html_module

    BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3",
                  "h4", "h5", "h6", "blockquote"}

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_starttag(self, tag, attrs):
            if tag in BLOCK_TAGS:
                self.parts.append("\n")

        def handle_endtag(self, tag):
            if tag in BLOCK_TAGS:
                self.parts.append("\n")

        def handle_data(self, data):
            self.parts.append(data)

    parser = _Stripper()
    parser.feed(html_str)
    text = html_module.unescape("".join(parser.parts))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines).strip()
