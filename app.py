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
def inject_role():
    email = session.get("email")

    return {
        "is_current_user_acceptor":
            bool(email and config.is_acceptor_email(email))
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

    email = email.lower()
    if email != "pradeep.singh1@dotpe.in":
        if ticket["Assigned To"].lower() != email:
        abort(403)

    if old_ticket is None:
        abort(404)

    new_assignee = request.form.get("transfer_to", "").strip()
    transfer_reason = request.form.get("transfer_reason", "").strip()

    if not new_assignee:
        flash("Please select an acceptor.", "danger")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    if new_assignee == old_ticket.get("Assigned To"):
        flash("Ticket is already assigned to this acceptor.", "warning")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    now_string = datetime.datetime.now(config.IST).strftime("%Y-%m-%d %H:%M:%S")

    new_ticket = sheets_utils.transfer_ticket(
        creds=creds,
        old_ticket=old_ticket,
        new_assignee=new_assignee,
        transfer_by=email,
        transfer_reason=transfer_reason,
        now_string=now_string,
    )

    flash(
        f"Ticket transferred successfully. New Ticket ID: {new_ticket['Ticket ID']}",
        "success",
    )

    return redirect(
        url_for(
            "ticket_detail",
            ticket_id=new_ticket["Ticket ID"],
        )
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
            "Attachment": ", ".join(attachment_names),
            "Updated Date": now_string,
            "Closed Date": "",
            "Acceptor Description":
                config.CONFIDENTIAL_TEXT if is_confidential else "",
            "Thread Id": sent["thread_id"],
            "RFC Message Id": rfc_message_id,
        }
        sheets_utils.append_ticket(creds, ticket)

        flash(f"Your ticket {ticket_id} has been raised successfully.", "success")

        return redirect(url_for("my_tickets"))

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

    tickets = [
        t for t in sheets_utils.get_all_tickets(creds)
        if t.get("Requestor Email") == email
    ]

    tickets.sort(key=lambda x: x.get("Created Date", ""), reverse=True)

    return render_template("my_tickets.html", tickets=tickets, email=email)


# ---------------------------------------------------------------------------
# ACCEPTOR DASHBOARD
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@acceptor_required
def dashboard():

    email, creds = current_user()

    tickets = sheets_utils.get_all_tickets(creds)

    email = email.lower()
    
    if email != "pradeep.singh1@dotpe.in":
        tickets = [
            t for t in tickets
            if t.get("Assigned To", "").lower() == email
        ]

    selected_status = request.args.get("status", "All")
    selected_priority = request.args.get("priority", "All")
    selected_assignee = request.args.get("assigned_to", "All")

    filtered = tickets

    if selected_status != "All":
        filtered = [t for t in filtered if t.get("Status") == selected_status]
    if selected_priority != "All":
        filtered = [t for t in filtered if t.get("Priority") == selected_priority]
    if selected_assignee != "All":
        filtered = [t for t in filtered if t.get("Assigned To") == selected_assignee]

    counts = {
        status: len([t for t in tickets if t.get("Status") == status])
        for status in config.STATUS_OPTIONS
    }

    total_tickets = len(tickets)
    open_tickets = counts.get("Open", 0)
    progress_tickets = counts.get("In Progress", 0)
    closed_tickets = counts.get("Closed", 0)
    resolved_tickets = counts.get("Resolved", 0)
    high_priority = len([t for t in tickets if t.get("Priority") == "High"])

    filtered.sort(key=lambda x: x.get("Updated Date", ""), reverse=True)

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

    ticket = sheets_utils.get_ticket(creds, ticket_id)

    email = email.lower()
    if email != "pradeep.singh1@dotpe.in":
        if ticket["Assigned To"].lower() != email:
        abort(403)

    if ticket is None:
        abort(404)

    return render_template(
        "ticket_detail.html",
        ticket=ticket,
        statuses=config.STATUS_OPTIONS,
        priorities=config.PRIORITY_OPTIONS,
        acceptors=team_status.get_assignable_acceptors(),
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

    email = email.lower()
    if email != "pradeep.singh1@dotpe.in":
        if ticket["Assigned To"].lower() != email:
        abort(403)

    if ticket is None:
        abort(404)

    old_status = ticket.get("Status", "")
    old_assignee = ticket.get("Assigned To", "")

    new_status = request.form.get("status", old_status)
    new_assignee = request.form.get("assigned_to", old_assignee)

    acceptor_note_html = request.form.get("acceptor_description_html", "").strip()

    # Quill's "empty" state is literally "<p><br></p>", not "".
    note_is_empty = acceptor_note_html in ("", "<p><br></p>")

    now = datetime.datetime.now(config.IST)
    now_string = now.strftime("%Y-%m-%d %H:%M:%S")

    updates = {"Updated Date": now_string}
    email_changes = []

    # --------------------------------------------
    # Status
    # --------------------------------------------

    if new_status != old_status:
        updates["Status"] = new_status
        email_changes.append(
            f"<li>Status changed from <b>{old_status}</b> to <b>{new_status}</b></li>"
        )
        if new_status in ("Resolved", "Closed"):
            updates["Closed Date"] = now_string

    # --------------------------------------------
    # Assignment
    # --------------------------------------------

    if new_assignee != old_assignee:
        updates["Assigned To"] = new_assignee
        email_changes.append(f"<li>Assigned to <b>{new_assignee}</b></li>")

    # --------------------------------------------
    # Notes — stored as PLAIN TEXT in the Sheet, sent as rich HTML in email
    # --------------------------------------------

    if not note_is_empty:
        existing = ticket.get("Acceptor Description", "")
        note_plain = sheets_utils.html_to_plain_text(acceptor_note_html)

        entry = (
            f"{now_string} - {email}\n"
            f"{note_plain}\n"
            "-----------------------------------\n"
        )

        updates["Acceptor Description"] = (existing + "\n" + entry) if existing else entry
        email_changes.append("<li>Comment Added</li>")

    # --------------------------------------------
    # Nothing changed
    # --------------------------------------------

    if len(updates) == 1:
        flash("Nothing to update.", "warning")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    # --------------------------------------------
    # Update the Sheet (reuses the ticket dict already fetched above —
    # no need to re-read the whole sheet to find the row again)
    # --------------------------------------------

    sheets_utils.update_ticket_fields(creds, ticket_id, updates, ticket=ticket)

    # --------------------------------------------
    # Email the requestor
    # --------------------------------------------

    signature = gmail_utils.get_signature(creds)

    update_html = ""
    if email_changes:
        update_html = "<ul>" + "".join(email_changes) + "</ul>"

    note_html = ""
    if not note_is_empty:
        note_html = f"<hr><b>Comment</b><br><br>{acceptor_note_html}"

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
            attachments.append({"filename": file.filename, "data": file.read()})

    rfc_message_id = ticket.get("RFC Message Id")

    if rfc_message_id:
        gmail_utils.send_threaded_reply(
            creds,
            to=ticket["Requestor Email"],
            subject=f"[{ticket_id}] {ticket['Subject']}",
            html_body=body,
            rfc_message_id=rfc_message_id,
            cc=",".join(default_cc) if default_cc else None,
            attachments=attachments,
        )
    else:
        # No RFC message id on record (e.g. a ticket created before this
        # column existed) — send as a fresh email instead of failing.
        gmail_utils.send_new_ticket_email(
            creds,
            to=ticket["Requestor Email"],
            subject=f"[{ticket_id}] {ticket['Subject']}",
            html_body=body,
            cc=",".join(default_cc) if default_cc else None,
            attachments=attachments,
        )
        flash(
            "Note: this ticket had no recorded email thread, so the "
            "update was sent as a new email rather than a threaded reply.",
            "warning",
        )

    flash("Ticket updated successfully.", "success")

    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


print("\nREGISTERED ROUTES\n")
for rule in app.url_map.iter_rules():
    print(rule.endpoint,"->",rule.rule)
print("\nEND ROUTES\n")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
