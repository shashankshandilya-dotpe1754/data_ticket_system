"""
Everything related to composing / sending / threading Gmail messages
using the logged-in user's Gmail account.

Optimized for low memory usage.
"""

import os
import base64
import mimetypes

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from googleapiclient.discovery import build


# ==========================================================
# Gmail Service
# ==========================================================

def gmail_service(creds):
    """
    Build Gmail service once per request.
    """
    return build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False
    )


# ==========================================================
# Gmail Signature
# ==========================================================

def get_signature(service):

    try:

        result = (
            service.users()
            .settings()
            .sendAs()
            .list(userId="me")
            .execute()
        )

        for item in result.get("sendAs", []):

            if item.get("isDefault"):

                return item.get("signature", "")

        if result.get("sendAs"):

            return result["sendAs"][0].get(
                "signature",
                ""
            )

    except Exception:

        pass

    return ""


# ==========================================================
# Build MIME Message
# ==========================================================

def _build_mime(
    to,
    subject,
    html_body,
    cc=None,
    bcc=None,
    attachments=None,
    in_reply_to=None,
    references=None,
):

    message = MIMEMultipart()

    message["To"] = to
    message["Subject"] = subject

    if cc:
        message["Cc"] = cc

    if bcc:
        message["Bcc"] = bcc

    if in_reply_to:
        message["In-Reply-To"] = in_reply_to

    if references:
        message["References"] = references

    message.attach(
        MIMEText(html_body, "html")
    )

    # -------------------------
    # Attachments
    # -------------------------

    if attachments:

        for file in attachments:

            filename = file["filename"]

            content = (
                file["data"]
                if "data" in file
                else open(file["path"], "rb").read()
            )

            mime_type = mimetypes.guess_type(filename)[0]

            if mime_type:

                maintype, subtype = mime_type.split("/", 1)

            else:

                maintype = "application"
                subtype = "octet-stream"

            part = MIMEBase(
                maintype,
                subtype
            )

            part.set_payload(content)

            encoders.encode_base64(part)

            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"'
            )

            message.attach(part)

    raw = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    return {
        "raw": raw
    }


# ==========================================================
# Send New Ticket
# ==========================================================

def send_new_ticket_email(
    service,
    to,
    subject,
    html_body,
    cc=None,
    bcc=None,
    attachments=None,
):

    body = _build_mime(
        to=to,
        subject=subject,
        html_body=html_body,
        cc=cc,
        bcc=bcc,
        attachments=attachments,
    )

    sent = (
        service.users()
        .messages()
        .send(
            userId="me",
            body=body
        )
        .execute()
    )

    return {

        "thread_id": sent["threadId"],

        "message_id": sent["id"]

    }


# ==========================================================
# Thread Reply
# ==========================================================

def send_threaded_reply(
    service,
    to,
    subject,
    html_body,
    thread_id,
    rfc_message_id,
    cc=None,
    bcc=None,
    attachments=None,
):

    if not subject.lower().startswith("re:"):

        subject = f"Re: {subject}"

    body = _build_mime(

        to=to,

        subject=subject,

        html_body=html_body,

        cc=cc,

        bcc=bcc,

        attachments=attachments,

        in_reply_to=rfc_message_id,

        references=rfc_message_id,

    )

    body["threadId"] = thread_id

    return (

        service.users()

        .messages()

        .send(

            userId="me",

            body=body

        )

        .execute()

    )


# ==========================================================
# RFC Message-ID
# ==========================================================

def get_rfc_message_id(
    service,
    gmail_message_id,
):

    result = (

        service.users()

        .messages()

        .get(

            userId="me",

            id=gmail_message_id,

            format="metadata",

            metadataHeaders=["Message-ID"],

        )

        .execute()

    )

    headers = result.get(

        "payload",

        {}

    ).get(

        "headers",

        []

    )

    for header in headers:

        if header["name"].lower() == "message-id":

            return header["value"]

    return ""
