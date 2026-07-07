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

import config
import auth
import gmail_utils
import sheets_utils
import team_status

app = Flask(__name__)

app.secret_key = config.SECRET_KEY

app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

# Stay logged in until explicit logout, instead of Flask's default (a
# session cookie that dies the moment the browser closes).
app.config["PERMANENT_SESSION_LIFETIME"] = config.PERMANENT_SESSION_LIFETIME
app.config["SESSION_REFRESH_EACH_REQUEST"] = True

os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)


# ==========================================================
# Authentication Helpers
# ==========================================================

@app.context_processor
def inject_globals():

    email = session.get("email", "").lower()

    try:
        acceptors=team_status.get_assignable_acceptors(creds),
    except Exception:
        acceptors = []

    return {

        "is_current_user_acceptor":
            config.is_acceptor_email(email),

        "is_admin": email.lower() in [
            x.lower() for x in config.MANAGE_ACCESS_USERS
        ],

        "acceptors":
            acceptors,
    }


def current_user():

    email = session.get("email")

    if not email:
        return None, None

    creds = auth.credentials_from_dict(session.get("credentials"))

    if creds is None:
        session.clear()
        return None, None

    # Save refreshed credentials back into the session
    session["credentials"] = auth.credentials_to_dict(creds)

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
        include_granted_scopes="false",
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
    print("=" * 80)
    print("FLOW CLIENT ID")
    print(creds.client_id)
    print("FLOW TOKEN")
    print(creds.token)
    print("=" * 80)
    print("Refresh Token:", creds.refresh_token)
    print("=" * 80)
    print("TOKEN")
    print(creds.token)
    print("=" * 80)
    print("REFRESH TOKEN")
    print(creds.refresh_token)
    print("=" * 80)
    print("SCOPES")
    print(creds.scopes)
    print("=" * 80)
    email = auth.get_user_email(creds)

    session.permanent = True

    session["credentials"] = auth.credentials_to_dict(creds)
    print(session["credentials"])
    session["email"] = email

    if auth.is_acceptor(email):
        team_status.register_acceptor_login(email)
        return redirect(url_for("dashboard"))
    return redirect(url_for("my_tickets"))



# ==========================================================
# Add/Delete Acceptor
# ==========================================================
@app.route("/acceptor/add", methods=["POST"])
@acceptor_required
def add_acceptor():
    email, creds = current_user()
    new_email=request.form.get("email","").strip().lower()
    if not new_email:
        flash("Please enter an email.","warning")
        return redirect(url_for("dashboard"))
    sheets_utils.add_acceptor(creds,new_email)
    flash("Acceptor added successfully.","success")
    return redirect(url_for("dashboard"))

@app.route("/acceptor/delete", methods=["POST"])
@acceptor_required
def delete_acceptor():
    email, creds = current_user()
    remove_email=request.form.get("email","").strip().lower()
    if remove_email==email.lower():
        flash("You cannot delete yourself.","warning")
        return redirect(url_for("dashboard"))
    sheets_utils.delete_acceptor(creds,remove_email)
    flash("Acceptor deleted successfully.","success")
    return redirect(url_for("dashboard"))

# ==========================================================
# Transfer Ticket
# ==========================================================
@app.route("/transfer_ticket/<ticket_id>", methods=["POST"])
@acceptor_required
def transfer_ticket(ticket_id):

    email, creds = current_user()

    old_ticket = sheets_utils.get_ticket(creds, ticket_id)

    if old_ticket is None:
        abort(404)

    email = email.lower()

    # --------------------------------------------------
    # Permission Check
    # --------------------------------------------------
    if email != "pradeep.singh1@dotpe.in":

        if old_ticket.get("Assigned To", "").strip().lower() != email:
            abort(403)

    # --------------------------------------------------
    # Form Values
    # --------------------------------------------------
    new_assignee = request.form.get("transfer_to", "").strip().lower()
    transfer_reason = request.form.get("transfer_reason", "").strip()

    if not new_assignee:

        flash("Please select an acceptor.", "danger")

        return redirect(
            url_for(
                "ticket_detail",
                ticket_id=ticket_id,
            )
        )

    if new_assignee == old_ticket.get("Assigned To", "").strip().lower():

        flash(
            "Ticket is already assigned to this acceptor.",
            "warning",
        )

        return redirect(
            url_for(
                "ticket_detail",
                ticket_id=ticket_id,
            )
        )

    # --------------------------------------------------
    # Create New Ticket
    # --------------------------------------------------
    now_string = datetime.datetime.now(config.IST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    new_ticket = sheets_utils.transfer_ticket(
        creds=creds,
        old_ticket=old_ticket,
        new_assignee=new_assignee,
        transfer_by=email,
        transfer_reason=transfer_reason,
        now_string=now_string,
    )

    # --------------------------------------------------
    # Success Message
    # --------------------------------------------------
    flash(
        f"Ticket transferred successfully.<br>"
        f"<b>New Ticket ID:</b> {new_ticket['Ticket ID']}<br>"
        f"<b>Assigned To:</b> {new_assignee}",
        "success",
    )

    # --------------------------------------------------
    # Redirect
    # --------------------------------------------------
    # The transferred ticket now belongs to another
    # acceptor, so redirect back to the dashboard instead
    # of opening a ticket the current user no longer owns.
    return redirect(url_for("dashboard"))


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
        description_html = request.form.get("description_html", "")
        priority = request.form.get("priority", "Medium")
        high_priority_reason = request.form.get("high_priority_reason", "").strip()
        cc = request.form.get("cc", "").strip()
        bcc = request.form.get("bcc", "").strip()

        is_confidential = request.form.get("is_confidential") == "on"

        # -----------------------------------------
        # Validation
        # -----------------------------------------

        if subject == "":
            flash("Subject cannot be empty.", "error")
            return redirect(url_for("new_ticket"))

        if description_html.strip() == "":
            flash("Description cannot be empty.", "error")
            return redirect(url_for("new_ticket"))

        if priority == "High" and high_priority_reason == "":
            flash("High Priority Reason is mandatory.", "error")
            return render_template(
                "requestor_form.html",
                priorities=config.PRIORITY_OPTIONS,
                form=request.form,
                banner=banner,
            )

        # -----------------------------------------
        # Ticket ID + timestamp (IST)
        # -----------------------------------------

        ticket_id = sheets_utils.next_ticket_id(creds)
        now = datetime.datetime.now(config.IST)
        now_string = now.strftime("%Y-%m-%d %H:%M:%S")

        # -----------------------------------------
        # Attachments
        # -----------------------------------------

        attachments = []
        attachment_names = []

        for file in request.files.getlist("attachments"):
            if file.filename == "":
                continue
            filename = secure_filename(file.filename)
            save_path = os.path.join(config.UPLOAD_FOLDER, f"{ticket_id}_{filename}")
            file.save(save_path)
            attachments.append({"filename": filename, "path": save_path})
            attachment_names.append(filename)

        # -----------------------------------------
        # Email (rich HTML — separate from what's stored in the Sheet)
        # -----------------------------------------
        if is_confidential:
            assigned_to = config.CONFIDENTIAL_ASSIGNEE
            mail_receivers = [config.CONFIDENTIAL_ASSIGNEE]    # Only Pradeep receives confidential tickets
            cc = ""
            bcc = ""
        else:
            assigned_to = ""
            mail_receivers = sheets_utils.get_acceptors(creds)    # Send notification to every acceptor from Google Sheet
            mail_receivers = list(dict.fromkeys(mail_receivers))  # Remove duplicates if any
        
        signature = gmail_utils.get_signature(creds)
        email_subject = f"[{ticket_id}] {subject}"

        banner_html = ""
        if banner:
            banner_html = f"""
            <div style="background:#fff3cd;border:1px solid #ffc107;
                        padding:12px;border-radius:6px;margin-bottom:15px;">
                <b>Notice</b><br>{banner}
            </div>
            """

        email_body = f"""
        <h3>Data Team Ticket</h3>
        <table>
        <tr><td><b>Ticket ID</b></td><td>{ticket_id}</td></tr>
        <tr><td><b>Priority</b></td><td>{priority}</td></tr>
        <tr><td><b>Status</b></td><td>Open</td></tr>
        <tr><td><b>Raised By</b></td><td>{email}</td></tr>
        <tr><td><b>Created</b></td><td>{now_string}</td></tr>
        </table>
        {banner_html}
        <hr>
        {description_html}
        <br><br>
        {signature}
        """

        sent = gmail_utils.send_new_ticket_email(
            creds,
            to=", ".join(mail_receivers),
            subject=email_subject,
            html_body=email_body,
            cc=cc if cc else None,
            bcc=bcc if bcc else None,
            attachments=attachments,
        )

        rfc_message_id = gmail_utils.get_rfc_message_id(creds, sent["message_id"])

        # -----------------------------------------
        # Sheet storage — PLAIN TEXT description, not raw HTML
        # -----------------------------------------

        ticket = {
            "Ticket ID": ticket_id,
            "Created Date": now_string,
            
            "Requestor Email":
                config.CONFIDENTIAL_TEXT if is_confidential else email,
            
            "Subject":
                config.CONFIDENTIAL_TEXT if is_confidential else subject,
            
            "Requestor Description":
                config.CONFIDENTIAL_TEXT if is_confidential
                else sheets_utils.html_to_plain_text(description_html),
            
            "Priority": priority,
            
            "High Priority Reason":
                config.CONFIDENTIAL_TEXT if is_confidential
            else high_priority_reason,
            "Status": "Open",
            "Assigned To": assigned_to,
            "Attachments": ", ".join(attachment_names),
            "Updated Date": now_string,
            "Closed Date": "",
            "Acceptor Description":
                config.CONFIDENTIAL_TEXT if is_confidential else "",
            "Thread Id": sent["thread_id"],
            "RFC Message Id": rfc_message_id,
        }
        sheets_utils.append_ticket(creds, ticket)

        sheets_utils.append_conversation_message(
            creds,
            {
                "Ticket ID": ticket_id,
                "Message Time": now_string,
                "Sender Email": email,
                "Sender Name": email.split("@")[0],
                "Sender Type": "Requestor",
                "Message": sheets_utils.html_to_plain_text(description_html),
                "HTML": description_html,
                "Attachments": ", ".join(attachment_names),
            },
        )       

        flash(f"Your ticket {ticket_id} has been raised successfully.", "success")

        return redirect(url_for("my_tickets"))

    return render_template(
        "requestor_form.html",
        priorities=config.PRIORITY_OPTIONS,
        form={},
        banner=banner,
    )


# ---------------------------------------------------------------------------
# MY TICKETS
# ---------------------------------------------------------------------------

@app.route("/my-tickets")
@login_required
def my_tickets():

    email, creds = current_user()

    tickets = [
        t for t in sheets_utils.get_all_tickets(creds)
        if t.get("Requestor Email") == email
    ]

    # -----------------------------
    # Read Filters
    # -----------------------------

    created_date = request.args.get("created_date", "")
    assigned_to = request.args.get("assigned_to", "")
    priority = request.args.get("priority", "")
    status = request.args.get("status", "")
    search = request.args.get("search", "").strip().lower()

    # -----------------------------
    # Apply Filters
    # -----------------------------

    if created_date:
        tickets = [
            t for t in tickets
            if t.get("Created Date", "").startswith(created_date)
        ]

    if assigned_to:
        tickets = [
            t for t in tickets
            if t.get("Assigned To", "") == assigned_to
        ]

    if priority:
        tickets = [
            t for t in tickets
            if t.get("Priority", "") == priority
        ]

    if status:
        tickets = [
            t for t in tickets
            if t.get("Status", "") == status
        ]

    # -----------------------------
    # Search
    # -----------------------------

    if search:

        searchable_columns = [

            "Ticket ID",
            "Subject",
            "Parent Ticket ID",
            "Previous Ticket ID",

        ]

        tickets = [

            t

            for t in tickets

            if any(
                search in str(t.get(col, "")).lower()
                for col in searchable_columns
            )

        ]

    tickets.sort(
        key=lambda x: x.get("Created Date", ""),
        reverse=True
    )

    return render_template(
        "my_tickets.html",
        tickets=tickets,
        email=email,
        acceptors=team_status.get_assignable_acceptors(creds),
        priorities=config.PRIORITY_OPTIONS,
        statuses=config.STATUS_OPTIONS,
        current_created_date=created_date,
        current_assignee=assigned_to,
        current_priority=priority,
        current_status=status,
        current_search=search,
    )


# ---------------------------------------------------------------------------
# REQUESTOR TICKET DETAILS
# ---------------------------------------------------------------------------

@app.route("/my-ticket/<ticket_id>")
@login_required
def my_ticket_detail(ticket_id):

    email, creds = current_user()

    ticket = sheets_utils.get_ticket(creds, ticket_id)

    if ticket is None:
        abort(404)

    if ticket["Requestor Email"].lower() != email.lower():
        abort(403)

    conversation = sheets_utils.get_conversation(
        creds,
        ticket_id,
    )

    return render_template(
        "requestor_ticket_detail.html",
        ticket=ticket,
        conversation=conversation,
    )


# ---------------------------------------------------------------------------
# ACCEPTOR DASHBOARD
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@acceptor_required
def dashboard():

    email, creds = current_user()

    tickets = sheets_utils.get_all_tickets(creds)

    email = email.lower()
    # Pradeep can see all tickets
    if email != "pradeep.singh1@dotpe.in":
        tickets = [
            t for t in tickets
            if (
                str(t.get("Requestor Email", "")).strip().lower()
                != config.CONFIDENTIAL_TEXT.lower()
            )
            and
            (
                t.get("Assigned To", "").strip() == ""
                or
                t.get("Assigned To", "").strip().lower() == email
            )
        ]

    # -----------------------------
    # Filters
    # -----------------------------
    selected_status = request.args.get("status", "All")
    selected_priority = request.args.get("priority", "All")
    selected_assignee = request.args.get("assigned_to", "All")
    selected_created_date = request.args.get("created_date", "")
    search = request.args.get("search", "").strip().lower()

    filtered = tickets

    if selected_status != "All":
        filtered = [
            t for t in filtered
            if t.get("Status") == selected_status
        ]

    if selected_priority != "All":
        filtered = [
            t for t in filtered
            if t.get("Priority") == selected_priority
        ]

    if selected_assignee != "All":
        filtered = [
            t for t in filtered
            if t.get("Assigned To") == selected_assignee
        ]

    # -----------------------------
    # Created Date Filter
    # -----------------------------
    if selected_created_date:
        filtered = [
            t for t in filtered
            if t.get("Created Date", "").startswith(selected_created_date)
        ]

    # -----------------------------
    # Global Search
    # -----------------------------
    if search:

        searchable_columns = [
            "Ticket ID",
            "Requestor Email",
            "Subject",
            "Requestor Description",
            "High Priority Reason",
            "Parent Ticket ID",
            "Previous Ticket ID",
        ]

        filtered = [
            t
            for t in filtered
            if any(
                search in str(t.get(col, "")).lower()
                for col in searchable_columns
            )
        ]

    # -----------------------------
    # Dashboard Counts
    # -----------------------------
    counts = {
        status: len([
            t for t in tickets
            if t.get("Status") == status
        ])
        for status in config.STATUS_OPTIONS
    }

    total_tickets = len(tickets)
    open_tickets = counts.get("Open", 0)
    progress_tickets = counts.get("In Progress", 0)
    resolved_tickets = counts.get("Resolved", 0)
    closed_tickets = counts.get("Closed", 0)

    high_priority = len([
        t for t in tickets
        if t.get("Priority") == "High"
    ])

    filtered.sort(
        key=lambda x: x.get("Updated Date", ""),
        reverse=True,
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
        acceptors=team_status.get_assignable_acceptors(creds),
        availability=team_status.get_availability(),
        current_status=selected_status,
        current_priority=selected_priority,
        current_assignee=selected_assignee,
        current_created_date=selected_created_date,
        current_search=search,
    )

# ---------------------------------------------------------------------------
# Ticket Details
# ---------------------------------------------------------------------------

@app.route("/ticket/<ticket_id>")
@acceptor_required
def ticket_detail(ticket_id):

    email, creds = current_user()

    ticket = sheets_utils.get_ticket(creds, ticket_id)

    if ticket is None:
        abort(404)
        
    email = email.lower()
    # Pradeep can open every ticket
    if email != "pradeep.singh1@dotpe.in":
        if (
            str(ticket.get("Requestor Email", "")).strip().lower()
            == config.CONFIDENTIAL_TEXT.lower()
        ):
            abort(403)
        
        assigned_to = ticket.get("Assigned To", "").strip().lower()
        if assigned_to not in ("", email):
            abort(403)
            
    conversation = sheets_utils.get_conversation(
        creds,
        ticket_id,
    )
    
    return render_template(
        "ticket_detail.html",
        ticket=ticket,
        conversation=conversation,
        statuses=config.STATUS_OPTIONS,
        priorities=config.PRIORITY_OPTIONS,
        acceptors=team_status.get_assignable_acceptors(creds),
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
        status = request.form.get("status", "Available")
        note = request.form.get("note", "")

        team_status.set_availability(status=status, note=note, set_by=email)

        flash("Availability updated successfully.", "success")
        return redirect(url_for("dashboard"))

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

    ticket = sheets_utils.get_ticket(creds, ticket_id)

    if ticket is None:
        abort(404)

    email = email.lower()

    # --------------------------------------------------
    # Permission Check
    # --------------------------------------------------

    # Pradeep can update everything.
    # Other acceptors:
    #   - Cannot update Confidential tickets.
    #   - Can update unassigned tickets (first pickup).
    #   - Can update only their own assigned tickets.
    if email != "pradeep.singh1@dotpe.in":

        if (
            str(ticket.get("Requestor Email", "")).strip().lower()
            == config.CONFIDENTIAL_TEXT.lower()
        ):
            abort(403)

        assigned_to = ticket.get("Assigned To", "").strip().lower()

        if assigned_to not in ("", email):
            abort(403)

    # --------------------------------------------------
    # Existing values
    # --------------------------------------------------

    old_status = ticket.get("Status", "")
    old_assignee = ticket.get("Assigned To", "").strip()

    new_status = request.form.get("status", old_status)
    new_assignee = request.form.get("assigned_to", old_assignee)

    # --------------------------------------------------
    # Auto Pickup
    # --------------------------------------------------

    # If ticket is unassigned, automatically assign it
    # to the acceptor who is updating it.
    if old_assignee == "":
        new_assignee = email

    acceptor_note_html = request.form.get(
        "acceptor_description_html", ""
    ).strip()

    note_is_empty = acceptor_note_html in ("", "<p><br></p>")

    now = datetime.datetime.now(config.IST)
    now_string = now.strftime("%Y-%m-%d %H:%M:%S")

    updates = {
        "Updated Date": now_string
    }

    email_changes = []

    # --------------------------------------------------
    # Status
    # --------------------------------------------------

    if new_status != old_status:

        updates["Status"] = new_status

        email_changes.append(
            f"<li>Status changed from <b>{old_status}</b> to <b>{new_status}</b></li>"
        )

        if new_status in ("Resolved", "Closed"):
            updates["Closed Date"] = now_string

    # --------------------------------------------------
    # Assignment
    # --------------------------------------------------

    if new_assignee != old_assignee:

        updates["Assigned To"] = new_assignee

        email_changes.append(
            f"<li>Assigned to <b>{new_assignee}</b></li>"
        )

    # --------------------------------------------------
    # Notes
    # --------------------------------------------------

    if not note_is_empty:

        existing = ticket.get("Acceptor Description", "")

        note_plain = sheets_utils.html_to_plain_text(
            acceptor_note_html
        )

        entry = (
            f"{now_string} - {email}\n"
            f"{note_plain}\n"
            "-----------------------------------\n"
        )

        updates["Acceptor Description"] = (
            existing + "\n" + entry
            if existing
            else entry
        )

        email_changes.append("<li>Comment Added</li>")

    
    # --------------------------------------------------
    # Save Conversation
    # --------------------------------------------------
        
    if not note_is_empty:
        
        sheets_utils.append_conversation_message(
            creds,
            {
                "Ticket ID": ticket_id,
                "Message Time": now_string,
                "Sender Email": email,
                "Sender Name": email.split("@")[0],
                "Sender Type": "Acceptor",
                "Message": note_plain,
                "HTML": acceptor_note_html,
                "Attachments": ", ".join(
                    f.filename
                    for f in request.files.getlist("attachments")
                    if f.filename
                ),
            },
        )

    # --------------------------------------------------
    # Nothing changed
    # --------------------------------------------------

    if len(updates) == 1:

        flash("Nothing to update.", "warning")

        return redirect(
            url_for(
                "ticket_detail",
                ticket_id=ticket_id,
            )
        )

    # --------------------------------------------------
    # Update Google Sheet
    # --------------------------------------------------

    sheets_utils.update_ticket_fields(
        creds,
        ticket_id,
        updates,
        ticket=ticket,
    )

    # --------------------------------------------------
    # Email Requestor
    # --------------------------------------------------

    signature = gmail_utils.get_signature(creds)

    update_html = ""

    if email_changes:
        update_html = "<ul>" + "".join(email_changes) + "</ul>"

    note_html = ""

    if not note_is_empty:
        note_html = (
            f"<hr><b>Comment</b><br><br>"
            f"{acceptor_note_html}"
        )

    body = f"""
    <h3>Ticket Updated</h3>

    <p><b>Ticket ID :</b> {ticket_id}</p>

    {update_html}

    {note_html}

    <br><br>

    {signature}
    """

    default_cc = config.default_cc_for_assignee(new_assignee)

    attachments = []

    for file in request.files.getlist("attachments"):

        if file and file.filename:

            attachments.append(
                {
                    "filename": file.filename,
                    "data": file.read(),
                }
            )
    
    gmail_message_id = gmail_utils.search_ticket_email(
        creds,
        ticket_id,
    )
    
    if gmail_message_id:
        
        metadata = gmail_utils.get_message_metadata(
            creds,
            gmail_message_id,
        )
        
        gmail_utils.send_threaded_reply(
            
            creds=creds,
            thread_id=metadata["thread_id"],
            rfc_message_id=metadata["rfc_message_id"],
            to=ticket["Requestor Email"],
            subject=f"[{ticket_id}] {ticket['Subject']}",
            html_body=body,
            cc=",".join(default_cc) if default_cc else None,
            attachments=attachments,
        )
    
    else:
        gmail_utils.send_new_ticket_email(
            
            creds=creds,
            to=ticket["Requestor Email"],
            subject=f"[{ticket_id}] {ticket['Subject']}",
            html_body=body,
            cc=",".join(default_cc) if default_cc else None,
            attachments=attachments,
        )
        
        flash(
            "Original ticket email not found in your mailbox. Update sent as a new email.",
            "warning",
        )
        
        flash("Ticket updated successfully.", "success")

    return redirect(
        url_for(
            "ticket_detail",
            ticket_id=ticket_id,
        )
    )

print("\nREGISTERED ROUTES\n")
for rule in app.url_map.iter_rules():
    print(rule.endpoint,"->",rule.rule)
print("\nEND ROUTES\n")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
