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
app.config["PERMANENT_SESSION_LIFETIME"] = config.PERMANENT_SESSION_LIFETIME
app.config["SESSION_REFRESH_EACH_REQUEST"] = True

os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)


def display_first_name(email: str) -> str:
    """'shashank.shandilya@dotpe.in' -> 'Shashank'"""
    local = email.split("@")[0]
    first = local.split(".")[0]
    return first.capitalize() if first else email


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
        # Either no valid data, or the refresh token was revoked/expired
        # (see auth.py) — either way, treat as logged out.
        session.clear()
        return None, None
    session["credentials"] = auth.credentials_to_dict(creds)
    return session.get("email"), creds


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        email, creds = current_user()
        if email is None:
            flash("Please login with Google first.", "warning")
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


def acceptor_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        email, creds = current_user()
        if email is None:
            flash("Please login first.", "warning")
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
        prompt="consent",
    )
    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/oauth2callback")
def oauth2callback():
    state = session.get("oauth_state")
    flow = auth.build_flow(state=state)
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    email = auth.get_user_email(creds)

    session.permanent = True
    session["credentials"] = auth.credentials_to_dict(creds)
    session["email"] = email

    if auth.is_acceptor(email):
        team_status.register_acceptor_login(email)
        return redirect(url_for("dashboard"))
    return redirect(url_for("my_tickets"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


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

        ticket_id = sheets_utils.next_ticket_id(creds)
        now = datetime.datetime.now(config.IST)
        now_string = now.strftime("%Y-%m-%d %H:%M:%S")

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
            to=", ".join(config.RECEIVERS),
            subject=email_subject,
            html_body=email_body,
            cc=cc if cc else None,
            bcc=bcc if bcc else None,
            attachments=attachments,
        )

        rfc_message_id = gmail_utils.get_rfc_message_id(creds, sent["message_id"])

        ticket = {
            "Ticket ID": ticket_id,
            "Created Date": now_string,
            "Requestor Email": email,
            "Subject": subject,
            "Requestor Description": sheets_utils.html_to_plain_text(description_html),
            "Priority": priority,
            "High Priority Reason": high_priority_reason,
            "Status": "Open",
            "Assigned To": "",
            "Attachments": ", ".join(attachment_names),
            "Updated Date": now_string,
            "Closed Date": "",
            "Parent Ticket ID": "",
            "Previous Ticket ID": "",
            "Transfer By": "",
            "Transfer Date": "",
            "Transfer Reason": "",
            "Acceptor Description": "",
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
        s: len([t for t in tickets if t.get("Status") == s])
        for s in config.STATUS_OPTIONS
    }

    filtered.sort(key=lambda x: x.get("Updated Date", ""), reverse=True)

    return render_template(
        "acceptor_dashboard.html",
        email=email,
        tickets=filtered,
        counts=counts,
        total_tickets=len(tickets),
        open_tickets=counts.get("Open", 0),
        progress_tickets=counts.get("In Progress", 0),
        resolved_tickets=counts.get("Resolved", 0),
        closed_tickets=counts.get("Closed", 0),
        high_priority=len([t for t in tickets if t.get("Priority") == "High"]),
        statuses=config.STATUS_OPTIONS,
        priorities=config.PRIORITY_OPTIONS,
        acceptors=team_status.get_assignable_acceptors(),
        availability=team_status.get_availability(),
        current_status=selected_status,
        current_priority=selected_priority,
        current_assignee=selected_assignee,
    )


@app.route("/ticket/<ticket_id>")
@acceptor_required
def ticket_detail(ticket_id):
    email, creds = current_user()
    ticket = sheets_utils.get_ticket(creds, ticket_id)
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
# UPDATE TICKET - status / notes, ALWAYS in the same original thread,
# never opens a new email chain.
# ---------------------------------------------------------------------------

@app.route("/ticket/<ticket_id>/update", methods=["POST"])
@acceptor_required
def update_ticket(ticket_id):

    email, creds = current_user()
    ticket = sheets_utils.get_ticket(creds, ticket_id)
    if ticket is None:
        abort(404)

    old_status = ticket.get("Status", "")
    old_assignee = ticket.get("Assigned To", "")
    new_status = request.form.get("status", old_status)
    new_assignee = request.form.get("assigned_to", old_assignee)

    acceptor_note_html = request.form.get("acceptor_description_html", "").strip()
    note_is_empty = acceptor_note_html in ("", "<p><br></p>")

    now = datetime.datetime.now(config.IST)
    now_string = now.strftime("%Y-%m-%d %H:%M:%S")

    updates = {"Updated Date": now_string}
    requestor_name = display_first_name(ticket["Requestor Email"])
    status_line = ""

    if new_status != old_status:
        updates["Status"] = new_status
        status_line = (
            f"<p>Hi {requestor_name},<br>"
            f"Your ticket <b>{ticket_id}</b> current status: <b>{new_status}</b></p>"
        )
        if new_status in ("Resolved", "Closed"):
            updates["Closed Date"] = now_string
        if new_status == "Closed":
            status_line = f"""
            <p>Hi {requestor_name},</p>
            <p>This ticket: <b>{ticket_id}</b> has been closed by <b>{email}</b>.</p>
            <p>Thank you for using our services. Have a great day! &#128522;</p>
            """

    if new_assignee != old_assignee:
        updates["Assigned To"] = new_assignee

    note_line = ""
    if not note_is_empty:
        note_plain = sheets_utils.html_to_plain_text(acceptor_note_html)
        existing = ticket.get("Acceptor Description", "")
        entry = f"{now_string} - {email}\n{note_plain}\n-----------------------------------\n"
        updates["Acceptor Description"] = (existing + "\n" + entry) if existing else entry
        note_line = f"<hr><b>Update from {email}:</b><br><br>{acceptor_note_html}"

    if len(updates) == 1:
        flash("Nothing to update.", "warning")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    sheets_utils.update_ticket_fields(creds, ticket_id, updates, ticket=ticket)

    # -----------------------------------------
    # Always reply in the SAME original thread - never a new email chain
    # -----------------------------------------

    signature = gmail_utils.get_signature(creds)
    body = f"{status_line}{note_line}<br><br>{signature}"

    default_cc = config.default_cc_for_assignee(new_assignee)
    rfc_message_id = ticket.get("RFC Message Id")

    attachments = []
    for file in request.files.getlist("attachments"):
        if file and file.filename:
            attachments.append({"filename": file.filename, "data": file.read()})

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


# ---------------------------------------------------------------------------
# TRANSFER TICKET - creates a NEW Ticket ID + NEW Sheet row + NEW email
# thread under the new assignee. The OLD thread gets one final notice.
# ---------------------------------------------------------------------------

@app.route("/ticket/<ticket_id>/transfer", methods=["POST"])
@acceptor_required
def transfer_ticket(ticket_id):

    email, creds = current_user()
    old_ticket = sheets_utils.get_ticket(creds, ticket_id)
    if old_ticket is None:
        abort(404)

    new_assignee = request.form.get("transfer_to", "").strip()
    transfer_reason = request.form.get("transfer_reason", "").strip()

    if not new_assignee:
        flash("Please select an acceptor to transfer to.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    now = datetime.datetime.now(config.IST)
    now_string = now.strftime("%Y-%m-%d %H:%M:%S")

    new_ticket = sheets_utils.transfer_ticket(
        creds, old_ticket, new_assignee, email, transfer_reason, now_string,
    )

    requestor_email = old_ticket["Requestor Email"]
    requestor_name = display_first_name(requestor_email)
    signature = gmail_utils.get_signature(creds)

    # 1) Final notice in the OLD thread
    old_rfc = old_ticket.get("RFC Message Id")
    old_notice_body = f"""
    <p>Hi {requestor_name},</p>
    <p>Your ticket has been transferred from <b>{email}</b> to
    <b>{new_assignee}</b> with new Ticket ID: <b>{new_ticket['Ticket ID']}</b>.</p>
    <p>You'll receive a new email for the new ticket shortly, and it will
    also appear in your <b>My Tickets</b> list.</p>
    <br><br>{signature}
    """
    if old_rfc:
        gmail_utils.send_threaded_reply(
            creds,
            to=requestor_email,
            subject=f"[{ticket_id}] {old_ticket['Subject']}",
            html_body=old_notice_body,
            rfc_message_id=old_rfc,
        )

    # 2) Brand-new thread for the new ticket.
    #    NOTE (technical constraint): this is sent from the TRANSFERRING
    #    acceptor's own Gmail - the app cannot send "as" the new assignee
    #    unless they are the one logged in and acting.
    default_cc = config.default_cc_for_assignee(new_assignee)
    new_subject = f"[{new_ticket['Ticket ID']}] {old_ticket['Subject']}"
    new_body = f"""
    <p>Hi {requestor_name},</p>
    <p>Your new Ticket ID: <b>{new_ticket['Ticket ID']}</b></p>
    <p><b>{new_assignee}</b> will be taking it forward from here.</p>
    <hr>
    <p><b>Original Description:</b></p>
    <p>{new_ticket.get('Requestor Description', '')}</p>
    <br><br>{signature}
    """

    sent = gmail_utils.send_new_ticket_email(
        creds,
        to=requestor_email,
        subject=new_subject,
        html_body=new_body,
        cc=",".join(default_cc) if default_cc else None,
    )
    rfc_message_id = gmail_utils.get_rfc_message_id(creds, sent["message_id"])

    sheets_utils.update_ticket_fields(
        creds,
        new_ticket["Ticket ID"],
        {"Thread Id": sent["thread_id"], "RFC Message Id": rfc_message_id},
        ticket=new_ticket,
    )

    flash(
        f"Ticket transferred. New Ticket ID: {new_ticket['Ticket ID']} "
        f"assigned to {new_assignee}.",
        "success",
    )
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
