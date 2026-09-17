"""Sends the report through the Gmail API as the authenticated user.

Using Gmail (rather than SMTP relay or a transactional provider) means the
message is created inside the sender's own mailbox, so it lands in Sent and
threads/replies behave normally.
"""
from __future__ import annotations

import base64
import logging
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


class MailError(RuntimeError):
    pass


def _credentials(client_id: str, client_secret: str, refresh_token: str) -> Credentials:
    missing = [
        name
        for name, value in (
            ("GMAIL_CLIENT_ID", client_id),
            ("GMAIL_CLIENT_SECRET", client_secret),
            ("GMAIL_REFRESH_TOKEN", refresh_token),
        )
        if not value
    ]
    if missing:
        raise MailError(f"Gmail credentials missing: {', '.join(missing)}")
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def build_message(
    *,
    sender: str,
    sender_name: str,
    to: list[str],
    cc: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    attachment: tuple[str, bytes] | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = f"{sender_name} <{sender}>" if sender_name else sender
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    if attachment:
        filename, payload = attachment
        message.add_attachment(
            payload,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename,
        )
    return message


def send(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    message: EmailMessage,
) -> str:
    """Send via Gmail; returns the created message id."""
    if not message["To"]:
        raise MailError("No recipients configured")
    service = build(
        "gmail",
        "v1",
        credentials=_credentials(client_id, client_secret, refresh_token),
        cache_discovery=False,
    )
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    log.info("Gmail message sent: id=%s to=%s", sent.get("id"), message["To"])
    return sent.get("id", "")
