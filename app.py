import os
import datetime
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    abort,
)

from werkzeug.utils import secure_filename
from bs4 import BeautifulSoup
import config
import auth
import gmail_utils
import sheets_utils
import team_status

app = Flask(__name__)

app.secret_key = config.SECRET_KEY

app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

# Requirement 1: stay logged in until explicit logout, instead of Flask's
# default (a session cookie that dies the moment the browser closes).
app.config["PERMANENT_SESSION_LIFETIME"] = config.PERMANENT_SESSION_LIFETIME
app.config["SESSION_REFRESH_EACH_REQUEST"] = True

os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)


# ==========================================================
# Authentication Helpers
# ==========================================================

@app.context_processor
def inject_role():
    email = session.get("email")

    return {
        "is_current_user_acceptor":
            bool(email and config.is_acceptor_email(email))
    }


def current_user():
    data = session.get("credentials")
    if not data:
        return None, None
    creds = auth.credentials_from_dict(data)
    if not creds:
        session.clear()
        return None, None
    # refresh may have rotated the access token — persist it back
    session["credentials"] = auth.credentials_to_dict(creds)
    return session.get("email"), creds


def login_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        email, creds = current_user()

        if email is None:

            flash(
                "Please login with Google first.",
                "warning"
            )

            return redirect(url_for("login"))

        return func(*args, **kwargs)

    return wrapper


def acceptor_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        email, creds = current_user()

        if email is None:

            flash(
                "Please login first.",
                "warning"
            )

            return redirect(url_for("login"))

        if not config.is_acceptor_email(email):

            abort(403)

        return func(*args, **kwargs)

    return wrapper

# ==========================================================
# Login
# ==========================================================

@app.route("/login")
def login():
    flow = auth.build_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",   # forces Google to re-issue a refresh_token every time
    )
    session["oauth_state"] = state
    return redirect(auth_url)

# ==========================================================
# OAuth Callback
# ==========================================================

@app.route("/oauth2callback")
def oauth2callback():
    state = session.get("oauth_state")
    flow = auth.build_flow(state=state)
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    email = auth.get_user_email(creds)

    # Requirement 1: mark this session permanent BEFORE writing to it, so
    # the browser stores it as a long-lived cookie (PERMANENT_SESSION_LIFETIME)
    # instead of a session-only cookie that dies on browser close.
    session.permanent = True

    session["credentials"] = auth.credentials_to_dict(creds)
    session["email"] = email

    if auth.is_acceptor(email):
        team_status.register_acceptor_login(email)
        return redirect(url_for("dashboard"))
    return redirect(url_for("my_tickets"))

# ==========================================================
# Logout
# ==========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("home"))


# ==========================================================
# Home
# ==========================================================

@app.route("/")
def home():

    email, creds = current_user()

    if email is None:

        return render_template("login.html")

    if auth.is_acceptor(email):

        return redirect(url_for("dashboard"))

    return redirect(url_for("my_tickets"))


# ---------------------------------------------------------------------------
# REQUESTOR UI
# ---------------------------------------------------------------------------

@app.route("/new-ticket", methods=["GET", "POST"])
@login_required
def new_ticket():

    email, creds = current_user()

    banner = team_status.availability_banner()

    if request.method == "POST":

        subject = request.form.get("subject", "").strip()

        description_html = request.form.get(
            "description_html",
            ""
        )
        description_text = BeautifulSoup(
            description_html,
            "html.parser"
        ).get_text("\n")

        priority = request.form.get(
            "priority",
            "Medium"
        )

        high_priority_reason = request.form.get(
            "high_priority_reason",
            ""
        ).strip()

        cc = request.form.get(
            "cc",
            ""
        ).strip()

        bcc = request.form.get(
            "bcc",
            ""
        ).strip()

        # -----------------------------------------
        # Validation
        # -----------------------------------------

        if subject == "":

            flash(
                "Subject cannot be empty.",
                "error"
            )

            return redirect(
                url_for("new_ticket")
            )

        if description_html.strip() == "":

            flash(
                "Description cannot be empty.",
                "error"
            )

            return redirect(
                url_for("new_ticket")
            )

        if (
            priority == "High"
            and high_priority_reason == ""
        ):

            flash(
                "High Priority Reason is mandatory.",
                "error"
            )

            return render_template(
                "requestor_form.html",
                priorities=config.PRIORITY_OPTIONS,
                form=request.form,
                banner=banner,
            )

        # -----------------------------------------
        # Build ONE Sheets client and ONE Gmail client for this whole
        # request, and reuse them everywhere below (memory fix — the old
        # code built a fresh client for nearly every single call).
        # -----------------------------------------

        sheets_svc = sheets_utils.sheets_service(creds)
        gmail_svc = gmail_utils.gmail_service(creds)

        # -----------------------------------------
        # Ticket
        # -----------------------------------------

        ticket_id = sheets_utils.next_ticket_id(
            sheets_svc
        )

        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(
            ZoneInfo("Asia/Kolkata")
        )

        now_string = now.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # -----------------------------------------
        # Attachments
        # -----------------------------------------

        attachments = []

        attachment_names = []

        uploaded_files = request.files.getlist(
            "attachments"
        )

        for file in uploaded_files:

            if file.filename == "":
                continue

            filename = secure_filename(
                file.filename
            )

            save_path = os.path.join(

                config.UPLOAD_FOLDER,

                f"{ticket_id}_{filename}"

            )

            file.save(save_path)

            attachments.append({

                "filename": filename,

                "path": save_path

            })

            attachment_names.append(filename)

        # -----------------------------------------
        # Email
        # -----------------------------------------

        signature = gmail_utils.get_signature(
            gmail_svc
        )

        email_subject = (
            f"[{ticket_id}] {subject}"
        )

        banner_html = ""

        if banner:

            banner_html = f"""
            <div style="
                background:#fff3cd;
                border:1px solid #ffc107;
                padding:12px;
                border-radius:6px;
                margin-bottom:15px;">
                <b>Notice</b><br>
                {banner}
            </div>
            """

        email_body = f"""
        <h3>Data Team Ticket</h3>

        <table>

        <tr>
        <td><b>Ticket ID</b></td>
        <td>{ticket_id}</td>
        </tr>

        <tr>
        <td><b>Priority</b></td>
        <td>{priority}</td>
        </tr>

        <tr>
        <td><b>Status</b></td>
        <td>Open</td>
        </tr>

        <tr>
        <td><b>Raised By</b></td>
        <td>{email}</td>
        </tr>

        <tr>
        <td><b>Created</b></td>
        <td>{now_string}</td>
        </tr>

        </table>

        {banner_html}

        <hr>

        {description_html}

        <br><br>

        {signature}
        """

        sent = gmail_utils.send_new_ticket_email(

            service=gmail_svc,

            to=", ".join(config.RECEIVERS),

            subject=email_subject,

            html_body=email_body,

            cc=cc if cc else None,

            bcc=bcc if bcc else None,

            attachments=attachments,

        )

        rfc_message_id = gmail_utils.get_rfc_message_id(

            gmail_svc,

            sent["message_id"]

        )

        ticket = {
            "Ticket ID": ticket_id,
            "Created Date": now_string,
            "Requestor Email": email,
            "Subject": subject,
            "Requestor Description": description_text,
            "Priority": priority,
            "High Priority Reason": high_priority_reason,
            "Status": "Open",
            "Assigned To": "",
            "Attachment": ", ".join(attachment_names),
            "Updated Date": now_string,
            "Closed Date": "",
            "Acceptor Description": "",
            "Thread Id": sent["thread_id"],
            "RFC Message Id": rfc_message_id,
        }
        sheets_utils.append_ticket(sheets_svc, ticket)

        flash(

            f"Ticket {ticket_id} created successfully.",

            "success"

        )

        return redirect(

            url_for("my_tickets")

        )

    return render_template(

        "requestor_form.html",

        priorities=config.PRIORITY_OPTIONS,

        form={},

        banner=banner,

    )


@app.route("/my-tickets")
@login_required
def my_tickets():

    email, creds = current_user()

    sheets_svc = sheets_utils.sheets_service(creds)

    tickets = []

    for ticket in sheets_utils.get_all_tickets(
        sheets_svc
    ):

        if ticket.get(
            "Requestor Email"
        ) == email:

            tickets.append(ticket)

    tickets.sort(

        key=lambda x: x.get(
            "Created Date",
            ""
        ),

        reverse=True

    )

    return render_template(

        "my_tickets.html",

        tickets=tickets,

        email=email,

    )

# ---------------------------------------------------------------------------
# ACCEPTOR DASHBOARD
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@acceptor_required
def dashboard():

    email, creds = current_user()

    sheets_svc = sheets_utils.sheets_service(creds)

    tickets = sheets_utils.get_all_tickets(service)
    ticket = next(
        x for x in tickets
        if x["Ticket ID"] == ticket_id
    )

    # --------------------------------------------
    # Filters
    # --------------------------------------------

    selected_status = request.args.get(
        "status",
        "All"
    )

    selected_priority = request.args.get(
        "priority",
        "All"
    )

    selected_assignee = request.args.get(
        "assigned_to",
        "All"
    )

    filtered = tickets

    if selected_status != "All":

        filtered = [

            t

            for t in filtered

            if t.get("Status") == selected_status

        ]

    if selected_priority != "All":

        filtered = [

            t

            for t in filtered

            if t.get("Priority") == selected_priority

        ]

    if selected_assignee != "All":

        filtered = [

            t

            for t in filtered

            if t.get("Assigned To") == selected_assignee

        ]

    # --------------------------------------------
    # Dashboard Cards
    # --------------------------------------------

    counts = {}

    for status in config.STATUS_OPTIONS:

        counts[status] = len(

            [

                t

                for t in tickets

                if t.get("Status") == status

            ]

        )

    total_tickets = len(tickets)

    open_tickets = counts.get("Open", 0)

    progress_tickets = counts.get("In Progress", 0)

    closed_tickets = counts.get("Closed", 0)

    resolved_tickets = counts.get("Resolved", 0)

    high_priority = len(

        [

            t

            for t in tickets

            if t.get("Priority") == "High"

        ]

    )

    # --------------------------------------------
    # Sort Latest First
    # --------------------------------------------

    filtered.sort(

        key=lambda x: x.get(

            "Updated Date",

            ""

        ),

        reverse=True

    )

    return render_template(

        "acceptor_dashboard.html",

        email=email,

        tickets=filtered,

        counts=counts,

        total_tickets=total_tickets,

        open_tickets=open_tickets,

        progress_tickets=progress_tickets,

        resolved_tickets=resolved_tickets,

        closed_tickets=closed_tickets,

        high_priority=high_priority,

        statuses=config.STATUS_OPTIONS,

        priorities=config.PRIORITY_OPTIONS,

        acceptors=team_status.get_assignable_acceptors(),

        availability=team_status.get_availability(),

        current_status=selected_status,

        current_priority=selected_priority,

        current_assignee=selected_assignee,

    )


# ---------------------------------------------------------------------------
# Ticket Details
# ---------------------------------------------------------------------------

@app.route("/ticket/<ticket_id>")
@acceptor_required
def ticket_detail(ticket_id):

    email, creds = current_user()

    sheets_svc = sheets_utils.sheets_service(creds)

    ticket = sheets_utils.get_ticket(

        sheets_svc,

        ticket_id

    )

    if ticket is None:

        abort(404)

    assignable_acceptors = (

        team_status.get_assignable_acceptors()

    )

    return render_template(

        "ticket_detail.html",

        ticket=ticket,

        statuses=config.STATUS_OPTIONS,

        priorities=config.PRIORITY_OPTIONS,

        acceptors=assignable_acceptors,

        email=email,

    )


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

@app.route("/availability", methods=["GET", "POST"])
@acceptor_required
def availability():

    email, creds = current_user()

    if request.method == "POST":

        status = request.form.get(

            "status",

            "Available"

        )

        note = request.form.get(

            "note",

            ""

        )

        team_status.set_availability(

            status=status,

            note=note,

            set_by=email,

        )

        flash(

            "Availability updated successfully.",

            "success"

        )

        return redirect(

            url_for("dashboard")

        )

    return render_template(

        "availability.html",

        current=team_status.get_availability(),

        options=config.AVAILABILITY_OPTIONS,

    )

# ---------------------------------------------------------------------------
# UPDATE TICKET
# ---------------------------------------------------------------------------

@app.route("/ticket/<ticket_id>/update", methods=["POST"])
@acceptor_required
def update_ticket(ticket_id):

    email, creds = current_user()

    # ONE Sheets client and ONE Gmail client for this whole request.
    sheets_svc = sheets_utils.sheets_service(creds)
    gmail_svc = gmail_utils.gmail_service(creds)

    ticket = sheets_utils.get_ticket(
        sheets_svc,
        ticket_id,
    )

    if ticket is None:
        abort(404)

    old_status = ticket.get("Status", "")
    old_assignee = ticket.get("Assigned To", "")

    new_status = request.form.get(
        "status",
        old_status,
    )

    new_assignee = request.form.get(
        "assigned_to",
        old_assignee,
    )

    # Requirement 2: this now arrives as rich-text HTML from the Quill
    # editor in ticket_detail.html, same as the ticket description box.
    acceptor_note_html = request.form.get(
        "acceptor_description_html",
        ""
    ).strip()

    acceptor_description_text = BeautifulSoup(
        acceptor_description,
        "html.parser"
    ).get_text("\n")

    # Quill's "empty" state is literally "<p><br></p>", not "" — treat
    # that as no note too.
    note_is_empty = acceptor_note_html in ("", "<p><br></p>")

    now = datetime.datetime.now()

    now_string = now.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    updates = {

        "Updated Date": now_string

    }

    email_changes = []

    # --------------------------------------------
    # Status
    # --------------------------------------------

    if new_status != old_status:

        updates["Status"] = new_status

        email_changes.append(

            f"""
            <li>
            Status changed from
            <b>{old_status}</b>
            to
            <b>{new_status}</b>
            </li>
            """

        )

        if new_status in [

            "Resolved",

            "Closed"

        ]:

            updates["Closed Date"] = now_string

    # --------------------------------------------
    # Assignment
    # --------------------------------------------

    if new_assignee != old_assignee:

        updates["Assigned To"] = new_assignee

        email_changes.append(

            f"""
            <li>
            Assigned to
            <b>{new_assignee}</b>
            </li>
            """

        )

    # --------------------------------------------
    # Notes (now rich-text HTML, appended as a timestamped entry)
    # --------------------------------------------

    if not note_is_empty:

        existing = ticket.get(

            "Acceptor Description",

            ""

        )

        entry_html = f"""
<div style="margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #ddd;">
  <div style="color:#666;font-size:0.85em;margin-bottom:4px;">{now_string} — {email}</div>
  {acceptor_note_html}
</div>
"""

        updates["Acceptor Description"] = (
            entry_html + existing if existing else entry_html
        )

        email_changes.append(

            f"""
            <li>
            Comment Added
            </li>
            """

        )

    # --------------------------------------------
    # Nothing Changed
    # --------------------------------------------

    if len(updates) == 1:

        flash(

            "Nothing to update.",

            "warning"

        )

        return redirect(

            url_for(

                "ticket_detail",

                ticket_id=ticket_id

            )

        )

    # --------------------------------------------
    # Update Google Sheet
    # --------------------------------------------
    # Passing `ticket=ticket` (already fetched above) avoids re-reading
    # the entire sheet a second time just to find this row again.

    sheets_utils.update_ticket_fields(

        sheets_svc,

        ticket_id,

        updates,

        ticket=ticket,

    )

    # --------------------------------------------
    # Gmail Thread
    # --------------------------------------------
    # Thread info is read straight from the ticket's own Sheet row
    # (Thread Id / RFC Message Id columns) rather than a local file,
    # so it survives Render restarts/redeploys.

    thread = {
        "thread_id": ticket.get("Thread Id"),
        "rfc_message_id": ticket.get("RFC Message Id"),
    }

    signature = gmail_utils.get_signature(

        gmail_svc

    )

    update_html = ""

    if email_changes:

        update_html = "<ul>"

        update_html += "".join(

            email_changes

        )

        update_html += "</ul>"

    note_html = ""

    if not note_is_empty:

        note_html = f"""

<hr>

<b>Comment</b>

<br><br>

{acceptor_note_html}

"""

    body = f"""

<h3>

Ticket Updated

</h3>

<p>

<b>Ticket ID :</b>

{ticket_id}

</p>

{update_html}

{note_html}

<br><br>

{signature}

"""

    if thread.get("thread_id") and thread.get("rfc_message_id"):

    gmail_utils.send_threaded_reply(
        service=gmail_svc,
        to=ticket["Requestor Email"],
        subject=f"[{ticket_id}] {ticket['Subject']}",
        html_body=body,
        thread_id=thread["thread_id"],
        rfc_message_id=thread["rfc_message_id"],
        cc=",".join(default_cc) if default_cc else None,
    )

else:

    gmail_utils.send_new_ticket_email(
        service=gmail_svc,
        to=ticket["Requestor Email"],
        subject=f"[{ticket_id}] {ticket['Subject']}",
        html_body=body,
        cc=",".join(default_cc) if default_cc else None,
    )

    # --------------------------------------------
    # Success
    # --------------------------------------------

    flash(

        "Ticket updated successfully.",

        "success"

    )

    return redirect(

        url_for(

            "ticket_detail",

            ticket_id=ticket_id

        )

    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
