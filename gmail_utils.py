"""
Gmail operations using plain `requests` calls to the Gmail REST API,
instead of google-api-python-client.

WHY THIS REWRITE:
1. CRASH FIX: the old code passed `threadId` when sending a reply from a
   DIFFERENT Gmail account than the one that created the thread. Gmail's
   threadId is scoped to a single mailbox — reusing it from another
   account returns 404 "Requested entity was not found." Threading in
   the requestor's inbox is actually accomplished by the standard RFC822
   In-Reply-To / References headers (which we already set correctly),
   NOT by the threadId API parameter. So threadId is simply dropped here.
2. MEMORY FIX: google-api-python-client pulls in protobuf, google-api-core,
   and googleapis-common-protos — a heavy dependency chain for what is,
   in practice, four simple HTTP calls. Plain `requests` (already a
   dependency for OAuth) does the same job with a far smaller footprint.
"""

import base64
import mimetypes
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _headers(creds):
    return {"Authorization": f"Bearer {creds.token}"}


def get_signature(creds) -> str:
    """Fetch the user's default Gmail signature (HTML, including any
    embedded image) for their primary send-as address."""
    try:
        resp = requests.get(
            f"{GMAIL_API_BASE}/settings/sendAs",
            headers=_headers(creds),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for entry in data.get("sendAs", []):
            if entry.get("isDefault"):
                return entry.get("signature", "") or ""
        send_as = data.get("sendAs", [])
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


def send_new_ticket_email(creds, to, subject, html_body, cc=None, bcc=None,
                           attachments=None):
    body = _build_mime(to, subject, html_body, cc=cc, bcc=bcc,
                        attachments=attachments)
    resp = requests.post(
        f"{GMAIL_API_BASE}/messages/send",
        headers=_headers(creds),
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    sent = resp.json()
    return {"thread_id": sent.get("threadId"), "message_id": sent["id"]}


def send_threaded_reply(creds, to, subject, html_body, rfc_message_id,
                         cc=None, bcc=None, attachments=None):
    """No `threadId` parameter — see module docstring. Threading in the
    requestor's inbox comes from In-Reply-To/References matching the
    original message's RFC822 Message-ID."""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    body = _build_mime(
        to, subject, html_body, cc=cc, bcc=bcc, attachments=attachments,
        in_reply_to=rfc_message_id, references=rfc_message_id,
    )
    resp = requests.post(
        f"{GMAIL_API_BASE}/messages/send",
        headers=_headers(creds),
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_rfc_message_id(creds, gmail_message_id: str) -> str:
    resp = requests.get(
        f"{GMAIL_API_BASE}/messages/{gmail_message_id}",
        params={"format": "metadata", "metadataHeaders": "Message-ID"},
        headers=_headers(creds),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    for h in data.get("payload", {}).get("headers", []):
        if h["name"].lower() == "message-id":
            return h["value"]
    return ""
