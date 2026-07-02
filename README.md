# Data Team Ticket System (Gmail + Google Sheets)

A two-UI ticketing system:
- **Requestor UI** (`/new-ticket`, `/my-tickets`) — anyone in your org logs in
  with their own Gmail, files a ticket with a full rich-text description,
  optional attachments/CC/BCC, and a mandatory reason when Priority = High.
- **Acceptor UI** (`/dashboard`, `/ticket/<id>`) — Data Team members see a
  live dashboard, open a ticket, change status, transfer/assign it, and add
  notes — every action fires an email in the **same Gmail thread** to the
  requestor, sent from the acceptor's own Gmail account (with their real
  signature).
- Every ticket and every status change is written to your existing Google
  Sheet ("Data Request Portal") so the sheet is always your live record.

---

## 1. Google Cloud setup (one-time, ~10 minutes)

1. Go to https://console.cloud.google.com/ and create (or select) a project.
2. **APIs & Services → Library** → enable:
   - Gmail API
   - Google Sheets API
   - (optional) Google People API — not required, but harmless if enabled.
3. **APIs & Services → OAuth consent screen**
   - User type: **Internal** if you're on Google Workspace (recommended —
     restricts login to your company domain automatically), otherwise
     **External** + add each requestor/acceptor as a test user while in
     Testing mode, or publish the app after Google's verification if you
     need it open to everyone.
   - Add the scopes used by this app (see `config.py SCOPES`):
     `gmail.send`, `gmail.readonly`, `gmail.settings.basic`,
     `spreadsheets`, `userinfo.email`, `openid`.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Web application**
   - Authorized redirect URI: `http://localhost:5000/oauth2callback`
     (swap for your real domain when you deploy, e.g.
     `https://tickets.yourcompany.com/oauth2callback`, and update
     `OAUTH_REDIRECT_URI` in `config.py` / env var to match).
   - Download the JSON and save it as `client_secret.json` in this folder.
5. **Share your Google Sheet** ("Data Request Portal") with Edit access to
   every acceptor and requestor who will use the app (or, if Internal/
   Workspace, share with the whole domain) — the app uses each user's own
   OAuth token to write to it, so they each need Sheet edit permission.

## 2. Configure the app

Edit `config.py`:
- `ACCEPTORS` — list the Gmail addresses of your Data Team members.
- `TEAM_INBOX_EMAIL` — where new-ticket emails are sent (a Google Group of
  all acceptors works well, e.g. `data-team@yourcompany.com`).
- `SPREADSHEET_ID` / `SHEET_NAME` are already pre-filled to match your
  screenshot (`Tickets` tab, columns A–M).

## 3. Install & run

```bash
cd data_ticket_system
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export FLASK_SECRET_KEY="some-random-string"
python app.py
```

Visit `http://localhost:5000`. Click **Sign in with Google** — the first
account you log in with will be treated as a Requestor or Acceptor
automatically based on whether its email is in `config.ACCEPTORS`.

## 4. How each requirement is met

| # | Requirement | Implementation |
|---|---|---|
| 2 | Login with Gmail, ticket sent via user's own Gmail | `auth.py` OAuth flow; `gmail_utils.send_new_ticket_email` sends using the logged-in user's own credentials |
| 3 | All updates on the same mail thread | `gmail_utils.send_threaded_reply` uses Gmail's `threadId` + `In-Reply-To`/`References` headers, tracked in `.thread_store.json` per ticket |
| 4 | Acceptors can transfer tickets | `ticket_detail.html` "Assign / Transfer To" dropdown → `update_ticket` route |
| 5 | Acceptors can change status | Same form, "Status" dropdown |
| 6 | High Priority ⇒ mandatory reason | Client-side JS in `requestor_form.html` + server-side check in `app.py` |
| 7 | Unlimited rich-text description, attachments, links | Quill.js editor (full Gmail-like toolbar) + file upload input |
| 8 | Mail includes sender's own signature | `gmail_utils.get_signature` reads `users.settings.sendAs` |
| 9 | Created / Updated / Closed dates | Written on create and on every `update_ticket` call |
| 10 | Attachments optional | `<input type="file" multiple>`, no `required` attribute |
| 11 | CC/BCC optional | Plain text inputs, not required |
| 12 | Sheet as DB, live status sync | `sheets_utils.py` reads/writes the exact columns from your sheet |
| 13 | Dashboard shows all statuses | `acceptor_dashboard.html` status count cards + filterable table |
| 14 | Status-change email to requestor | `update_ticket` always emails the requestor after any change |
| 15 | Two separate UIs | `/new-ticket` + `/my-tickets` vs `/dashboard` + `/ticket/<id>`, gated by `login_required` / `acceptor_required` |
| 16 | Before/after office hours (10 AM–7 PM) or holiday-next-day notice | `team_status.py`; any acceptor sets a shared status at `/availability` (Available / Today-On Leave / Tomorrow-On Leave / Tomorrow-Holiday + free-text note). Shown as a banner on the ticket form + dashboard, and auto-included in the requestor's confirmation email whenever the ticket is raised outside 10:00–19:00 or the status isn't "Available" |
| 17 | "Assign To" list = actual logged-in acceptor emails | `team_status.register_acceptor_login()` runs in `oauth2callback` the moment an acceptor signs in; `get_assignable_acceptors()` builds the dropdown from that registry (falls back to `config.ACCEPTORS` only if nobody has logged in yet) |

## 5. Productionizing notes (important for a real rollout)

- **Token storage**: this demo stores each user's OAuth refresh token as a
  flat JSON file under `.tokens/`. For production, move this into an
  encrypted database column (tokens are sensitive — they grant Gmail send
  access).
- **Thread ID storage**: currently a local `.thread_store.json` file. For
  multi-server or production deployments, add two extra (can be hidden)
  columns to your sheet — e.g. `N: Thread Id`, `O: RFC Message Id` — and
  read/write them via `sheets_utils` instead, so thread continuity survives
  server restarts and scales across instances.
- **Concurrency**: Sheets API calls here are simple read-then-write; under
  heavy simultaneous use, consider batching or moving to a proper DB
  (Postgres/Firestore) with the Sheet as a periodic export instead of the
  live source of truth.
- **Domain-restricted login**: if you're on Google Workspace, set the OAuth
  consent screen to "Internal" so only your company's Gmail accounts can
  log in at all — this is the cleanest way to keep the ticket system
  private to your org.
- **HTTPS**: OAuth requires `https://` in production (Google won't allow
  `http://` redirect URIs outside `localhost`).
