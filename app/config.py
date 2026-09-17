"""Application configuration, loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_emails(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = value.replace(";", ",").split(",")
    return [item.strip() for item in raw if item and item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Pipedrive ---------------------------------------------------------
    pipedrive_api_token: str = ""
    pipedrive_domain: str = Field(
        default="api",
        description="Company subdomain, e.g. 'acme' for acme.pipedrive.com. "
        "'api' works for most accounts.",
    )
    pipedrive_pipeline_id: int | None = 1

    # --- Report ------------------------------------------------------------
    report_title: str = "Sales Pipeline Update"
    report_greeting_name: str = "John"
    report_sender_name: str = "Valeria"
    report_timezone: str = "Europe/London"

    # Currency fallbacks, used when the live FX lookup fails.
    fx_fallback_usd_gbp: float = 0.74
    fx_fallback_eur_gbp: float = 0.85
    fx_api_url: str = "https://api.frankfurter.dev/v1/latest?base=GBP&symbols=USD,EUR"

    # --- Email -------------------------------------------------------------
    mail_from: str = ""
    mail_from_name: str = "House of Marketers"
    # NoDecode: pydantic-settings would otherwise try to JSON-parse these
    # before the validator runs, so a plain "a@x.com, b@x.com" would fail.
    mail_to: Annotated[list[str], NoDecode] = []
    mail_cc: Annotated[list[str], NoDecode] = []
    mail_subject_template: str = "Sales Pipeline Update - {date}"

    # While TEST_MODE is on, every message is redirected to TEST_RECIPIENT and
    # the real To/Cc list is shown in a banner at the top of the email.
    test_mode: bool = True
    test_recipient: str = ""

    # --- Gmail OAuth -------------------------------------------------------
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""

    # --- Scheduling --------------------------------------------------------
    # Disabled until a cadence is confirmed. Set SCHEDULE_ENABLED=true and
    # SCHEDULE_CRON (5-field cron, in REPORT_TIMEZONE) to turn it on.
    schedule_enabled: bool = False
    schedule_cron: str = "0 9 * * WED"
    # Fortnightly: the cron fires weekly and this gate skips the off weeks.
    schedule_fortnightly: bool = True
    # ISO date of a week the report SHOULD go out; parity is measured from it.
    schedule_anchor_date: str = "2026-09-16"

    # --- Web ---------------------------------------------------------------
    port: int = 8000
    admin_token: str = ""

    @field_validator("mail_to", "mail_cc", mode="before")
    @classmethod
    def _parse_emails(cls, value):
        return _split_emails(value)

    @property
    def pipedrive_base_url(self) -> str:
        return f"https://{self.pipedrive_domain}.pipedrive.com"

    def resolved_recipients(self) -> tuple[list[str], list[str]]:
        """Return (to, cc) after applying TEST_MODE redirection."""
        if self.test_mode:
            target = self.test_recipient or self.mail_from
            return ([target] if target else [], [])
        return (list(self.mail_to), list(self.mail_cc))


@lru_cache
def get_settings() -> Settings:
    return Settings()
