"""
Everything related to composing / sending / threading Gmail messages
using the logged-in user's OWN Gmail account (via their OAuth token).

MEMORY NOTE: build("gmail", "v1", credentials=creds) parses Google's API
discovery document into memory every time it's called. The old version of
this file built a brand-new service object inside every single helper
function, so a single ticket creation triggered 3 separate builds, and a
single status update triggered 2. On a 512MB Render instance, that
repeated parsing was a meaningful contributor to OOM crashes.

Fix: every function below now takes an already-built `service` object
instead of raw `creds`. Callers build the service ONCE per request via
gmail_service(creds) and pass it into every helper that needs it.
"""

import base64
import mimetypes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from googleapiclient.discovery import build


def gmail_service(creds):
    """Call this ONCE per request, then pass the returned object into
    every other function below instead of calling this repeatedly."""
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_signature(service) -> str:
    """Fetch the user's default Gmail signature (HTML) for their primary
    send-as address. Falls back to empty string if none is configured."""
    try:
        result = service.users().settings().sendAs().list(userId="me").execute()
        for entry in result.get("sendAs", []):
            if entry.get("isDefault"):
                return entry.get("signature", "") or ""
        # fall back to first entry if no default flagged
        send_as = result.get("sendAs", [])
        if send_as:
            return send_as[0].get("signature", "") or ""
    except Exception:
        pass
    return ""


def _build_mime(to, subject, html_body, cc=None, bcc=None, attachments=None,
                 in_reply_to=None, references=None):
    msg = MIMEMultipart("mixed")
    msg["to"] = to
    if cc:
        msg["cc"] = cc
    if bcc:
        msg["bcc"] = bcc
    msg["subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_body, "html"))
    msg.attach(alt)

    for att in attachments or []:
        # att = {"filename": ..., "path": ...}  OR {"filename":..., "data": bytes}
        ctype, encoding = mimetypes.guess_type(att["filename"])
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        part = MIMEBase(maintype, subtype)
        if "path" in att:
            with open(att["path"], "rb") as f:
                part.set_payload(f.read())
        else:
            part.set_payload(att["data"])
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment",
                         filename=att["filename"])
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


def send_new_ticket_email(service, to, subject, html_body, cc=None, bcc=None,
                           attachments=None):
    """Sends the initial ticket-creation email and returns the Gmail
    threadId + messageId so replies can be threaded onto it later."""
    body = _build_mime(to, subject, html_body, cc=cc, bcc=bcc,
                        attachments=attachments)
    sent = service.users().messages().send(userId="me", body=body).execute()
    return {"thread_id": sent["id"] and sent.get("threadId"),
            "message_id": sent["id"]}


def send_threaded_reply(
    credentials,
    to_email,
    subject,
    html_body,
    thread_id=None,
    attachments=None,
):
    if attachments:

    for file in attachments:

        if not file or file.filename == "":
            continue

        part = MIMEBase("application", "octet-stream")
        part.set_payload(file.read())
        encoders.encode_base64(part)

        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{file.filename}"'
        )

        message.attach(part)

    """Sends a reply that Gmail will display in the SAME conversation
    thread as the original ticket email."""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    body = _build_mime(
        to, subject, html_body, cc=cc, bcc=bcc, attachments=attachments,
        in_reply_to=rfc_message_id, references=rfc_message_id,
    )
    body["threadId"] = thread_id
    sent = service.users().messages().send(userId="me", body=body).execute()
    return sent


def get_rfc_message_id(service, gmail_message_id: str) -> str:
    """Gmail API's own message id isn't the RFC822 Message-ID header value
    that In-Reply-To/References need — fetch the header explicitly."""
    msg = service.users().messages().get(
        userId="me", id=gmail_message_id, format="metadata",
        metadataHeaders=["Message-ID"],
    ).execute()
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == "message-id":
            return h["value"]
    return ""
