"""Recipient resolution and the fortnightly cadence gate."""
from __future__ import annotations

from datetime import date

import pytest

from app.config import Settings
from app.scheduler import is_due


def _settings(**overrides) -> Settings:
    base = dict(
        mail_from="valeria@example.com",
        mail_to="john@example.com, sam@example.com",
        mail_cc="ops@example.com",
        _env_file=None,
    )
    base.update(overrides)
    return Settings(**base)


def test_comma_and_semicolon_separated_recipients_are_parsed():
    settings = _settings(mail_to="a@x.com; b@x.com,c@x.com")
    assert settings.mail_to == ["a@x.com", "b@x.com", "c@x.com"]


def test_test_mode_redirects_everything_to_the_tester():
    settings = _settings(test_mode=True, test_recipient="me@example.com")
    assert settings.resolved_recipients() == (["me@example.com"], [])


def test_test_mode_falls_back_to_the_sender_when_no_tester_is_set():
    settings = _settings(test_mode=True, test_recipient="")
    assert settings.resolved_recipients() == (["valeria@example.com"], [])


def test_live_mode_uses_the_real_recipients():
    settings = _settings(test_mode=False)
    assert settings.resolved_recipients() == (
        ["john@example.com", "sam@example.com"],
        ["ops@example.com"],
    )


# -- fortnightly cadence ---------------------------------------------------
@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 9, 16), True),   # the anchor week itself
        (date(2026, 9, 18), True),   # later in the anchor week
        (date(2026, 9, 23), False),  # the week after - skipped
        (date(2026, 9, 30), True),   # two weeks on - sends
        (date(2026, 10, 7), False),
        (date(2026, 10, 14), True),
        (date(2026, 9, 2), True),    # parity also holds backwards
    ],
)
def test_fortnightly_gate_alternates_weeks(day, expected):
    settings = _settings(schedule_anchor_date="2026-09-16", schedule_fortnightly=True)
    assert is_due(settings, day) is expected


def test_weekly_mode_never_skips():
    settings = _settings(schedule_fortnightly=False)
    for offset in range(14):
        assert is_due(settings, date(2026, 9, 16 + offset if offset < 15 else 16)) is True


def test_an_unparseable_anchor_date_does_not_stop_the_report():
    settings = _settings(schedule_anchor_date="not-a-date")
    assert is_due(settings, date(2026, 9, 23)) is True


def test_recipients_load_from_the_environment(monkeypatch):
    """Regression: pydantic-settings used to JSON-decode these before the
    validator ran, so a plain comma-separated MAIL_TO crashed startup."""
    monkeypatch.setenv("MAIL_TO", "john@example.com, sam@example.com")
    monkeypatch.setenv("MAIL_CC", "ops@example.com")
    settings = Settings(_env_file=None)
    assert settings.mail_to == ["john@example.com", "sam@example.com"]
    assert settings.mail_cc == ["ops@example.com"]


def test_empty_recipient_env_vars_are_tolerated(monkeypatch):
    monkeypatch.setenv("MAIL_TO", "")
    monkeypatch.setenv("MAIL_CC", "")
    settings = Settings(_env_file=None)
    assert settings.mail_to == []
    assert settings.mail_cc == []


def test_the_period_wording_follows_the_cadence():
    """The email must not say "fortnight" while the scheduler runs weekly."""
    assert _settings(schedule_fortnightly=True).period_label == "fortnight"
    assert _settings(schedule_fortnightly=False).period_label == "week"
