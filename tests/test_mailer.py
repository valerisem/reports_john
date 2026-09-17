"""Message construction and transport selection."""
from __future__ import annotations

import smtplib

import pytest

from app import mailer


def _message(to=("john@example.com",), cc=()):
    return mailer.build_message(
        sender="valeria@example.com",
        sender_name="House of Marketers",
        to=list(to),
        cc=list(cc),
        subject="Sales Pipeline Update - 17 September 2026",
        html_body="<p>hello</p>",
        text_body="hello",
        attachment=("report.xlsx", b"PK\x03\x04fake"),
    )


def test_message_headers_and_alternatives():
    msg = _message(cc=("ops@example.com",))
    assert msg["From"] == "House of Marketers <valeria@example.com>"
    assert msg["To"] == "john@example.com"
    assert msg["Cc"] == "ops@example.com"
    assert msg["Subject"].startswith("Sales Pipeline Update")
    types = {part.get_content_type() for part in msg.walk()}
    assert "text/plain" in types and "text/html" in types


def test_workbook_is_attached_with_the_spreadsheet_mime_type():
    attachments = [p for p in _message().walk() if p.get_filename()]
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "report.xlsx"
    assert attachments[0].get_content_type() == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_sending_without_recipients_is_refused():
    with pytest.raises(mailer.MailError, match="No recipients"):
        mailer.send(message=_message(to=()), username="a@b.com", app_password="x")


def test_unknown_transport_is_refused():
    with pytest.raises(mailer.MailError, match="Unknown MAIL_TRANSPORT"):
        mailer.send(transport="carrier-pigeon", message=_message())


def test_missing_app_password_explains_where_to_get_one():
    with pytest.raises(mailer.MailError, match="apppasswords"):
        mailer.send(message=_message(), username="valeria@example.com", app_password="")


def test_app_password_spaces_are_stripped_before_login(monkeypatch):
    """Google displays App Passwords as 'abcd efgh ijkl mnop'; Gmail rejects the spaces."""
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            seen["host"], seen["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            seen["tls"] = True

        def login(self, user, password):
            seen["user"], seen["password"] = user, password

        def send_message(self, message):
            seen["to"] = message["To"]

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    mailer.send(
        message=_message(),
        username="valeria@example.com",
        app_password="abcd efgh ijkl mnop",
    )
    assert seen["password"] == "abcdefghijklmnop"
    assert seen["user"] == "valeria@example.com"
    assert seen["tls"] is True
    assert (seen["host"], seen["port"]) == ("smtp.gmail.com", 587)


def test_auth_failure_is_reported_with_guidance(monkeypatch):
    class FailingSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            raise smtplib.SMTPAuthenticationError(535, b"bad creds")

        def send_message(self, m):
            raise AssertionError("should not be reached")

    monkeypatch.setattr(smtplib, "SMTP", FailingSMTP)
    with pytest.raises(mailer.MailError, match="rejected the App Password"):
        mailer.send(
            message=_message(), username="valeria@example.com", app_password="abcd"
        )
