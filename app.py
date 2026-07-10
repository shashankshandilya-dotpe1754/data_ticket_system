# ==========================================================
# Import Libraries
# ==========================================================

import os
import json
import tempfile
from functools import wraps
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
)

from werkzeug.utils import secure_filename

import config
import auth
import gmail_utils
import sheets_utils


# ==========================================================
# Flask App
# ==========================================================

app = Flask(__name__)

app.secret_key = config.SECRET_KEY

app.config["UPLOAD_FOLDER"] = config.UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
app.permanent_session_lifetime = config.PERMANENT_SESSION_LIFETIME


# ==========================================================
# Create Upload Folder
# ==========================================================

os.makedirs(
    config.UPLOAD_FOLDER,
    exist_ok=True,
)


# ==========================================================
# Current User
# ==========================================================

def current_user():

    creds = auth.credentials_from_session(session)

    if not creds:
        return None, None

    email = auth.get_user_email(creds)

    return creds, email.lower()


# ==========================================================
# Login Required
# ==========================================================

def login_required(func):

    @wraps(func)

    def wrapper(*args, **kwargs):

        creds, email = current_user()

        if not creds:

            return redirect(url_for("login"))

        return func(*args, **kwargs)

    return wrapper


# ==========================================================
# Home
# ==========================================================

@app.route("/")

def home():

    creds, email = current_user()

    if creds:

        if config.is_acceptor_email(email):

            return redirect(url_for("dashboard"))

        return redirect(url_for("my_tickets"))

    return redirect(url_for("login"))


# ==========================================================
# Authentication Routes
# ==========================================================

@app.route("/login")
def login():

    authorization_url, state = auth.get_authorization_url()

    session["oauth_state"] = state

    return redirect(authorization_url)


@app.route("/oauth2callback")
def oauth2callback():

    state = session.get("oauth_state")

    creds = auth.fetch_token(
        request.url,
        state,
    )

    session["credentials"] = auth.credentials_to_dict(creds)

    email = auth.get_user_email(creds).lower()

    session["user_email"] = email

    if config.is_acceptor_email(email):

        return redirect(url_for("dashboard"))

    return redirect(url_for("my_tickets"))


@app.route("/logout")
def logout():

    session.clear()

    flash(
        "You have been logged out.",
        "success",
    )

    return redirect(url_for("login"))


# ==========================================================
# Dashboard (Acceptor)
# ==========================================================

@app.route("/dashboard")
@login_required
def dashboard():

    creds = current_user()

    tickets = sheets_utils.get_all_tickets(creds)

    acceptors = sheets_utils.get_acceptors(creds)

    user_email = session["user_email"].lower()

    # Only show tickets assigned to the logged-in acceptor
    tickets = [
        t for t in tickets
        if t.get("Assigned To", "").lower() == user_email
    ]

    status_filter = request.args.get("status", "").strip()

    priority_filter = request.args.get("priority", "").strip()

    if status_filter:

        tickets = [
            t for t in tickets
            if t["Status"] == status_filter
        ]

    if priority_filter:

        tickets = [
            t for t in tickets
            if t["Priority"] == priority_filter
        ]

    tickets.sort(
        key=lambda x: x.get("Updated Date", ""),
        reverse=True,
    )

    return render_template(
        "dashboard.html",
        tickets=tickets,
        acceptors=acceptors,
        statuses=config.STATUS_OPTIONS,
        priorities=config.PRIORITY_OPTIONS,
        status_filter=status_filter,
        priority_filter=priority_filter,
    )


# ==========================================================
# My Tickets (Requestor)
# ==========================================================

@app.route("/my-tickets")
@login_required
def my_tickets():

    creds = current_user()

    user_email = session["user_email"].lower()

    tickets = sheets_utils.get_all_tickets(creds)

    tickets = [
        t for t in tickets
        if t["Requestor Email"].lower() == user_email
    ]

    status_filter = request.args.get("status", "").strip()

    priority_filter = request.args.get("priority", "").strip()

    if status_filter:

        tickets = [
            t for t in tickets
            if t["Status"] == status_filter
        ]

    if priority_filter:

        tickets = [
            t for t in tickets
            if t["Priority"] == priority_filter
        ]

    tickets.sort(
        key=lambda x: x.get("Updated Date", ""),
        reverse=True,
    )

    return render_template(
        "my_tickets.html",
        tickets=tickets,
        statuses=config.STATUS_OPTIONS,
        priorities=config.PRIORITY_OPTIONS,
        status_filter=status_filter,
        priority_filter=priority_filter,
    )


# ==========================================================
# Update Ticket
# ==========================================================

@app.route("/ticket/<ticket_id>/update", methods=["POST"])
@login_required
def update_ticket(ticket_id):

    creds = current_user()

    ticket = sheets_utils.get_ticket(
        creds,
        ticket_id,
    )

    if not ticket:

        flash(
            "Ticket not found.",
            "danger",
        )

        return redirect(
            url_for("dashboard")
        )

    now = datetime.now(config.IST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    status = request.form.get(
        "status",
        ticket["Status"],
    )

    assigned_to = request.form.get(
        "assigned_to",
        ticket["Assigned To"],
    )

    html_reply = request.form.get(
        "acceptor_description_html",
        "",
    )

    plain_reply = sheets_utils.html_to_plain_text(
        html_reply
    )

    cc = request.form.getlist("cc")

    bcc = request.form.getlist("bcc")

    uploaded_files = []

    attachment_names = []

    for f in request.files.getlist("attachments"):

        if not f or not f.filename:

            continue

        filename = secure_filename(
            f.filename
        )

        filepath = os.path.join(
            config.UPLOAD_FOLDER,
            filename,
        )

        f.save(filepath)

        uploaded_files.append({

            "filename": filename,

            "path": filepath,

        })

        attachment_names.append(
            filename
        )

    signature = gmail_utils.get_signature(
        creds
    )

    html_body = f"""
    {html_reply}
    <br><br>
    {signature}
    """

    gmail_utils.send_threaded_reply(

        creds=creds,

        thread_id=ticket["Thread Id"],

        rfc_message_id=ticket["RFC Message Id"],

        to=ticket["Requestor Email"],

        subject=f"[{ticket['Ticket ID']}] {ticket['Subject']}",

        html_body=html_body,

        cc=",".join(cc),

        bcc=",".join(bcc),

        attachments=uploaded_files,

    )

    updates = {

        "Status": status,

        "Assigned To": assigned_to,

        "Updated Date": now,

        "Acceptor Description": plain_reply,

        "CC": ",".join(cc),

        "BCC": ",".join(bcc),

    }

    if status == "Closed":

        updates["Closed Date"] = now

    else:

        updates["Closed Date"] = ""

    sheets_utils.update_ticket_fields(

        creds,

        ticket_id,

        updates,

        ticket,

    )

    sheets_utils.append_conversation_message(

        creds,

        {

            "Ticket ID": ticket_id,

            "Sender Type": "Acceptor",

            "Sender Name": session["user_email"],

            "Message": plain_reply,

            "HTML": html_reply,

            "Message Time": now,

            "Attachments": ", ".join(
                attachment_names
            ),

        },

    )

    flash(

        "Ticket updated successfully.",

        "success",

    )

    return redirect(

        url_for(

            "ticket_detail",

            ticket_id=ticket_id,

        )

    )


# ==========================================================
# Update Ticket
# ==========================================================

@app.route("/ticket/<ticket_id>/update", methods=["POST"])
@login_required
def update_ticket(ticket_id):

    creds = current_user()

    ticket = sheets_utils.get_ticket(
        creds,
        ticket_id,
    )

    if not ticket:

        flash(
            "Ticket not found.",
            "danger",
        )

        return redirect(
            url_for("dashboard")
        )

    now = datetime.now(config.IST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    status = request.form.get(
        "status",
        ticket["Status"],
    )

    assigned_to = request.form.get(
        "assigned_to",
        ticket["Assigned To"],
    )

    html_reply = request.form.get(
        "acceptor_description_html",
        "",
    )

    plain_reply = sheets_utils.html_to_plain_text(
        html_reply
    )

    cc = request.form.getlist("cc")

    bcc = request.form.getlist("bcc")

    uploaded_files = []

    attachment_names = []

    for f in request.files.getlist("attachments"):

        if not f or not f.filename:

            continue

        filename = secure_filename(
            f.filename
        )

        filepath = os.path.join(
            config.UPLOAD_FOLDER,
            filename,
        )

        f.save(filepath)

        uploaded_files.append({

            "filename": filename,

            "path": filepath,

        })

        attachment_names.append(
            filename
        )
    signature = gmail_utils.get_signature(
        creds
    )

    html_body = f"""
    {html_reply}
    <br><br>
    {signature}
    """

    gmail_utils.send_threaded_reply(

        creds=creds,

        thread_id=ticket["Thread Id"],

        rfc_message_id=ticket["RFC Message Id"],

        to=ticket["Requestor Email"],

        subject=f"[{ticket['Ticket ID']}] {ticket['Subject']}",

        html_body=html_body,

        cc=",".join(cc),

        bcc=",".join(bcc),

        attachments=uploaded_files,

    )


    updates = {

        "Status": status,

        "Assigned To": assigned_to,

        "Updated Date": now,

        "Acceptor Description": plain_reply,

        "CC": ",".join(cc),

        "BCC": ",".join(bcc),

    }

    if status == "Closed":

        updates["Closed Date"] = now

    else:

        updates["Closed Date"] = ""

    sheets_utils.update_ticket_fields(

        creds,

        ticket_id,

        updates,

        ticket,

    )


    sheets_utils.append_conversation_message(

        creds,

        {

            "Ticket ID": ticket_id,

            "Sender Type": "Acceptor",

            "Sender Name": session["user_email"],

            "Message": plain_reply,

            "HTML": html_reply,

            "Message Time": now,

            "Attachments": ", ".join(
                attachment_names
            ),

        }

    )


    flash(

        "Ticket updated successfully.",

        "success",

    )

    return redirect(

        url_for(

            "ticket_detail",

            ticket_id=ticket_id,

        )

    )


# ==========================================================
# Requestor Reply
# ==========================================================

@app.route("/my-ticket/<ticket_id>/reply", methods=["POST"])
@login_required
def requestor_reply(ticket_id):

    creds = current_user()

    ticket = sheets_utils.get_ticket(
        creds,
        ticket_id,
    )

    if not ticket:

        flash(
            "Ticket not found.",
            "danger",
        )

        return redirect(
            url_for("my_tickets")
        )

    now = datetime.now(config.IST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    html_reply = request.form.get(
        "reply_html",
        "",
    )

    plain_reply = sheets_utils.html_to_plain_text(
        html_reply
    )

    uploaded_files = []

    attachment_names = []

    for f in request.files.getlist("attachments"):

        if not f or not f.filename:

            continue

        filename = secure_filename(
            f.filename
        )

        filepath = os.path.join(
            config.UPLOAD_FOLDER,
            filename,
        )

        f.save(filepath)

        uploaded_files.append({

            "filename": filename,

            "path": filepath,

        })

        attachment_names.append(
            filename
        )

    signature = gmail_utils.get_signature(
        creds
    )

    html_body = f"""
    {html_reply}
    <br><br>
    {signature}
    """

    cc = []

    if ticket.get("CC"):

        cc = [

            x.strip()

            for x in ticket["CC"].split(",")

            if x.strip()

        ]

    bcc = []

    if ticket.get("BCC"):

        bcc = [

            x.strip()

            for x in ticket["BCC"].split(",")

            if x.strip()

        ]

    gmail_utils.send_threaded_reply(

        creds=creds,

        thread_id=ticket["Thread Id"],

        rfc_message_id=ticket["RFC Message Id"],

        to=ticket["Assigned To"],

        subject=f"[{ticket['Ticket ID']}] {ticket['Subject']}",

        html_body=html_body,

        cc=",".join(cc),

        bcc=",".join(bcc),

        attachments=uploaded_files,

    )

    sheets_utils.update_ticket_fields(

        creds,

        ticket_id,

        {

            "Updated Date": now,

            "Requestor Description": plain_reply,

        },

        ticket,

    )

    sheets_utils.append_conversation_message(

        creds,

        {

            "Ticket ID": ticket_id,

            "Sender Type": "Requestor",

            "Sender Name": session["user_email"],

            "Message": plain_reply,

            "HTML": html_reply,

            "Message Time": now,

            "Attachments": ", ".join(
                attachment_names
            ),

        },

    )

    flash(

        "Reply sent successfully.",

        "success",

    )

    return redirect(

        url_for(

            "my_ticket_detail",

            ticket_id=ticket_id,

        )

    )


# ==========================================================
# Transfer Ticket
# ==========================================================

@app.route("/ticket/<ticket_id>/transfer", methods=["POST"])
@login_required
def transfer_ticket(ticket_id):

    creds = current_user()

    old_ticket = sheets_utils.get_ticket(
        creds,
        ticket_id,
    )

    if not old_ticket:

        flash(
            "Ticket not found.",
            "danger",
        )

        return redirect(
            url_for("dashboard")
        )

    new_assignee = request.form.get(
        "transfer_to",
        ""
    ).strip().lower()

    transfer_reason = request.form.get(
        "transfer_reason",
        ""
    ).strip()

    if not new_assignee:

        flash(
            "Please select an acceptor.",
            "warning",
        )

        return redirect(
            url_for(
                "ticket_detail",
                ticket_id=ticket_id,
            )
        )

    if new_assignee == old_ticket["Assigned To"].lower():

        flash(
            "Ticket is already assigned to this user.",
            "warning",
        )

        return redirect(
            url_for(
                "ticket_detail",
                ticket_id=ticket_id,
            )
        )

    now = datetime.now(config.IST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    new_ticket = sheets_utils.transfer_ticket(

        creds,

        old_ticket,

        new_assignee,

        session["user_email"],

        transfer_reason,

        now,

    )

    sheets_utils.update_ticket_fields(

        creds,

        new_ticket["Ticket ID"],

        {

            "Thread Id": old_ticket["Thread Id"],

            "RFC Message Id": old_ticket["RFC Message Id"],

            "CC": old_ticket.get("CC", ""),

            "BCC": old_ticket.get("BCC", ""),

        },

        new_ticket,

    )


    html_body = f"""
    <p>
        A ticket has been transferred to you.
    </p>

    <p>

        <strong>Ticket ID:</strong>
        {new_ticket["Ticket ID"]}

    </p>

    <p>

        <strong>Subject:</strong>
        {new_ticket["Subject"]}

    </p>

    <p>

        <strong>Transfer Reason:</strong><br>

        {transfer_reason or "-"}

    </p>
    """

    gmail_utils.send_new_ticket_email(

        creds,

        to=new_assignee,

        subject=f"[{new_ticket['Ticket ID']}] Ticket Transferred",

        html_body=html_body,

    )

    flash(

        "Ticket transferred successfully.",

        "success",

    )

    return redirect(

        url_for(

            "ticket_detail",

            ticket_id=new_ticket["Ticket ID"],

        )

    )


# ==========================================================
# Manage Acceptors
# ==========================================================

@app.route("/manage-access")
@login_required
def manage_access():

    user_email = session["user_email"].lower()

    if user_email not in config.MANAGE_ACCESS_USERS:

        flash(
            "Access denied.",
            "danger",
        )

        return redirect(url_for("dashboard"))

    creds = current_user()

    acceptors = sheets_utils.get_acceptors(creds)

    return render_template(

        "manage_access.html",

        acceptors=acceptors,

    )


@app.route("/manage-access/add", methods=["POST"])
@login_required
def add_acceptor():

    user_email = session["user_email"].lower()

    if user_email not in config.MANAGE_ACCESS_USERS:

        flash(
            "Access denied.",
            "danger",
        )

        return redirect(url_for("dashboard"))

    creds = current_user()

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    if not email:

        flash(
            "Email is required.",
            "warning",
        )

        return redirect(
            url_for("manage_access")
        )

    added = sheets_utils.add_acceptor(
        creds,
        email,
    )

    if added:

        flash(
            "Acceptor added successfully.",
            "success",
        )

    else:

        flash(
            "Acceptor already exists.",
            "warning",
        )

    return redirect(
        url_for("manage_access")
    )

@app.route("/manage-access/delete/<path:email>", methods=["POST"])
@login_required
def delete_acceptor(email):

    user_email = session["user_email"].lower()

    if user_email not in config.MANAGE_ACCESS_USERS:

        flash(
            "Access denied.",
            "danger",
        )

        return redirect(url_for("dashboard"))

    creds = current_user()

    deleted = sheets_utils.delete_acceptor(
        creds,
        email,
    )

    if deleted:

        flash(
            "Acceptor removed successfully.",
            "success",
        )

    else:

        flash(
            "Acceptor not found.",
            "warning",
        )

    return redirect(
        url_for("manage_access")
    )


@app.route("/manage-access/delete/<path:email>", methods=["POST"])
@login_required
def delete_acceptor(email):

    user_email = session["user_email"].lower()

    if user_email not in config.MANAGE_ACCESS_USERS:

        flash(
            "Access denied.",
            "danger",
        )

        return redirect(url_for("dashboard"))

    creds = current_user()

    deleted = sheets_utils.delete_acceptor(
        creds,
        email,
    )

    if deleted:

        flash(
            "Acceptor removed successfully.",
            "success",
        )

    else:

        flash(
            "Acceptor not found.",
            "warning",
        )

    return redirect(
        url_for("manage_access")
    )

# ==========================================================
# 404
# ==========================================================

@app.errorhandler(404)
def page_not_found(error):

    return (

        render_template(

            "404.html"

        ),

        404,

    )

# ==========================================================
# 500
# ==========================================================

@app.errorhandler(500)
def internal_error(error):

    return (

        render_template(

            "500.html"

        ),

        500,

    )

# ==========================================================
# Run
# ==========================================================

if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=5000,

        debug=True,

    )

if __name__ == "__main__":
    app.run(debug=True)

# ==========================================================
# Error Handlers
# ==========================================================

@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_server_error(error):
    return render_template("500.html"), 500

# ==========================================================
# Run Application
# ==========================================================

if __name__ == "__main__":
    app.run(debug=True)
