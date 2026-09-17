"""Sends the report from the user's own Gmail account.

Two transports, both of which send *as* the user so the message lands in their
Sent folder and replies come back to them normally:

* ``app_password`` (default) - Gmail SMTP with a 16-character App Password.
  Nothing to register; Gmail copies SMTP-sent mail into Sent automatically.
* ``oauth`` - the Gmail API with an OAuth refresh token. Needed if the Google
  Workspace admin has disabled App Passwords.
"""
from __future__ import annotations

import base64
import logging
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


class MailError(RuntimeError):
    pass


def _credentials(client_id: str, client_secret: str, refresh_token: str):
    # Imported lazily: the OAuth transport is optional, and google-auth pulls in
    # a large dependency tree that the App Password path never needs.
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

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


def _send_oauth(*, client_id: str, client_secret: str, refresh_token: str,
                message: EmailMessage) -> str:
    from googleapiclient.discovery import build

    service = build(
        "gmail",
        "v1",
        credentials=_credentials(client_id, client_secret, refresh_token),
        cache_discovery=False,
    )
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    log.info("Gmail message sent (oauth): id=%s to=%s", sent.get("id"), message["To"])
    return sent.get("id", "")


def _send_app_password(*, username: str, app_password: str, message: EmailMessage) -> str:
    if not username:
        raise MailError("MAIL_FROM is not set")
    if not app_password:
        raise MailError(
            "GMAIL_APP_PASSWORD is not set. Create one at "
            "https://myaccount.google.com/apppasswords (requires 2-Step Verification)."
        )
    # Gmail rejects the password if the spaces Google displays are left in.
    password = app_password.replace(" ", "")
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "Gmail rejected the App Password. Check MAIL_FROM is the account the "
            f"password was created for, and that the password is current. ({exc.smtp_code})"
        ) from exc
    log.info("Gmail message sent (app password) to=%s", message["To"])
    return ""


def send(
    *,
    transport: str = "app_password",
    message: EmailMessage,
    username: str = "",
    app_password: str = "",
    client_id: str = "",
    client_secret: str = "",
    refresh_token: str = "",
) -> str:
    """Send via Gmail. Returns the Gmail message id when the transport reports one."""
    if not message["To"]:
        raise MailError("No recipients configured")
    if transport == "oauth":
        return _send_oauth(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            message=message,
        )
    if transport == "app_password":
        return _send_app_password(
            username=username, app_password=app_password, message=message
        )
    raise MailError(f"Unknown MAIL_TRANSPORT {transport!r} (use 'app_password' or 'oauth')")
