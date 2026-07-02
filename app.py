import os
import json
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

import config
import auth
import gmail_utils
import sheets_utils
import team_status

app = Flask(__name__)

app.secret_key = config.SECRET_KEY

app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)


# ==========================================================
# Authentication Helpers
# ==========================================================

@app.context_processor
def inject_role():
    email = session.get("email")

    return {
        "is_current_user_acceptor":
            bool(email and auth.is_acceptor(email))
    }


def current_user():
    """
    Returns:
        (email, credentials)
        or
        (None, None)
    """

    email = session.get("email")

    if not email:
        return None, None

    creds = auth.load_credentials(email)

    if creds is None:

        session.clear()

        return None, None

    return email, creds


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

        if not auth.is_acceptor(email):

            abort(403)

        return func(*args, **kwargs)

    return wrapper

# ==========================================================
# Login
# ==========================================================

@app.route("/login")
def login():

    # Always clear previous OAuth state
    session.pop("oauth_state", None)

    flow = auth.build_flow()

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )

    session["oauth_state"] = state

    print("=" * 80)
    print("LOGIN")
    print("STATE :", state)
    print("AUTH URL REPR:")
    print(type(authorization_url))
    print(len(authorization_url))
    print(authorization_url)
    print(repr(authorization_url))
    print("=" * 80)

    if not authorization_url.startswith("https://"):
    raise Exception(f"Invalid OAuth URL: {authorization_url}")

    return redirect(authorization_url)

# ==========================================================
# OAuth Callback
# ==========================================================

@app.route("/oauth2callback")
def oauth2callback():

    try:

        if "error" in request.args:

            flash(
                f"Google Login Failed : {request.args['error']}",
                "danger"
            )

            return redirect(url_for("login"))

        if "code" not in request.args:

            flash(
                "Authorization code missing.",
                "danger"
            )

            return redirect(url_for("login"))

        state = session.get("oauth_state")

        if not state:

            flash(
                "OAuth session expired. Please login again.",
                "danger"
            )

            return redirect(url_for("login"))

        flow = auth.build_flow(state)

        print("=" * 80)
        print("CALLBACK")
        print("STATE :", state)
        print("REQUEST URL :", request.url)
        print("ARGS :", dict(request.args))
        print("=" * 80)

        flow.fetch_token(
            authorization_response=request.url
        )

        creds = flow.credentials

        email = auth.get_user_email(creds)

        auth.save_credentials(
            email,
            creds
        )

        session.clear()

        session["email"] = email

        print("=" * 80)
        print("LOGIN SUCCESS")
        print("EMAIL :", email)
        print("=" * 80)

        if auth.is_acceptor(email):

            team_status.register_acceptor_login(email)

            return redirect(
                url_for("dashboard")
            )

        return redirect(
            url_for("my_tickets")
        )

    except Exception as e:

        import traceback

        traceback.print_exc()

        flash(
            f"OAuth Error : {str(e)}",
            "danger"
        )

        return redirect(
            url_for("login")
        )


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
        # Ticket
        # -----------------------------------------

        ticket_id = sheets_utils.next_ticket_id(
            creds
        )

        now = datetime.datetime.now()

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
            creds
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

            creds=creds,

            to=", ".join(config.RECEIVERS),

            subject=email_subject,

            html_body=email_body,

            cc=cc if cc else None,

            bcc=bcc if bcc else None,

            attachments=attachments,

        )

        rfc_message_id = gmail_utils.get_rfc_message_id(

            creds,

            sent["message_id"]

        )

        ticket = {

            "Ticket ID": ticket_id,

            "Created Date": now_string,

            "Requestor Email": email,

            "Subject": subject,

            "Requestor Description": description_html,

            "Priority": priority,

            "High Priority Reason": high_priority_reason,

            "Status": "Open",

            "Assigned To": "",

            "Attachment": ", ".join(
                attachment_names
            ),

            "Updated Date": now_string,

            "Closed Date": "",

            "Acceptor Description": ""

        }

        sheets_utils.append_ticket(

            creds,

            ticket

        )

        _remember_thread(

            ticket_id,

            sent["thread_id"],

            rfc_message_id,

            email

        )

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

    tickets = []

    for ticket in sheets_utils.get_all_tickets(
        creds
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

    tickets = sheets_utils.get_all_tickets(creds)

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

    ticket = sheets_utils.get_ticket(

        creds,

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

    ticket = sheets_utils.get_ticket(
        creds,
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

    acceptor_note = request.form.get(
        "acceptor_description",
        ""
    ).strip()

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
    # Notes
    # --------------------------------------------

    if acceptor_note:

        existing = ticket.get(

            "Acceptor Description",

            ""

        )

        history = f"""

[{now_string}]

{email}

{acceptor_note}

"""

        if existing:

            history = existing + "\n" + history

        updates[

            "Acceptor Description"

        ] = history

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

    sheets_utils.update_ticket_fields(

        creds,

        ticket_id,

        updates,

    )

    # --------------------------------------------
    # Gmail Thread
    # --------------------------------------------

    thread = _get_thread(ticket_id)

    signature = gmail_utils.get_signature(

        creds

    )

    update_html = ""

    if email_changes:

        update_html = "<ul>"

        update_html += "".join(

            email_changes

        )

        update_html += "</ul>"

    note_html = ""

    if acceptor_note:

        note_html = f"""

<hr>

<b>Comment</b>

<br><br>

{acceptor_note}

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

    if thread:

        default_cc = config.default_cc_for_assignee(

            new_assignee

        )

        gmail_utils.send_threaded_reply(

            creds=creds,

            to=ticket["Requestor Email"],

            subject=f"[{ticket_id}] {ticket['Subject']}",

            html_body=body,

            thread_id=thread["thread_id"],

            rfc_message_id=thread["rfc_message_id"],

            cc=",".join(default_cc)

            if default_cc else None,

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

# ---------------------------------------------------------------------------
# THREAD BOOKKEEPING
# ---------------------------------------------------------------------------
# Gmail's threadId / RFC Message-ID need to be stored somewhere so replies
# land in the same conversation. Simplest robust option: two extra hidden
# columns in your sheet (N: Thread Id, O: RFC Message Id). This helper
# reads/writes those directly so you don't have to touch sheets_utils'
# COLUMNS list (which mirrors your visible sheet layout).
import json
THREAD_STORE = os.path.join(os.path.dirname(__file__), ".thread_store.json")


def _load_thread_store():
    if not os.path.exists(THREAD_STORE):
        return {}
    with open(THREAD_STORE) as f:
        return json.load(f)


def _remember_thread(ticket_id, thread_id, rfc_message_id, requestor_email):
    store = _load_thread_store()
    store[ticket_id] = {
        "thread_id": thread_id,
        "rfc_message_id": rfc_message_id,
        "requestor_email": requestor_email,
    }
    with open(THREAD_STORE, "w") as f:
        json.dump(store, f)


def _get_thread(ticket_id):
    return _load_thread_store().get(ticket_id)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
