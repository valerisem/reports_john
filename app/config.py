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
    # Override custom-field resolution when a field is named unusually.
    # Accepts a field key (40 hex chars) from GET /preview/fields.
    pipedrive_field_account_manager: str = ""
    pipedrive_field_industry: str = ""
    pipedrive_field_sub_industry: str = ""
    pipedrive_field_org_website: str = ""
    pipedrive_field_campaign_start: str = ""
    pipedrive_field_campaign_end: str = ""

    # --- Supabase (team directory: pods, owners, account managers) ---------
    supabase_url: str = "https://tciqyupqtpbhremuprxe.supabase.co"
    # The anon/publishable key is enough: team, roles and teams allow public
    # reads, and account_manager_orgs has no RLS.
    supabase_key: str = ""

    # --- Report ------------------------------------------------------------
    report_title: str = "Sales Pipeline Update"
    report_greeting_name: str = "John"
    report_sender_name: str = "Valeria"
    report_timezone: str = "Europe/London"

    # Some periods bring almost no new brands. Below this many, the email also
    # shows the biggest new business already in the pipeline, so the section is
    # never near-empty.
    new_brand_min: int = 3
    new_brand_fallback_count: int = 5

    # Header artwork and logo, so they can be swapped without a code change.
    header_image_url: str = (
        "https://tciqyupqtpbhremuprxe.supabase.co/storage/v1/object/public/"
        "reports%20for%20john/email-header_1.jpg"
    )
    # Mirrors the header: the background fades down into the photo so the
    # email ends on the picture. Empty leaves the plain logo footer.
    footer_image_url: str = ""
    logo_url: str = (
        "https://tciqyupqtpbhremuprxe.supabase.co/storage/v1/object/public/"
        "reports%20for%20john/email-logo.png"
    )
    # Die-cut head shots for the leaderboard, one per account owner. {name} is
    # their first name in lower case, so the default resolves to
    # ".../reports for john/valeriia.png". Transparent PNGs: a JPEG cannot
    # carry transparency and shows its background behind the head. An owner
    # with no file simply appears without a picture.
    owner_photo_url_template: str = (
        "https://tciqyupqtpbhremuprxe.supabase.co/storage/v1/object/public/"
        "reports%20for%20john/{name}.png"
    )

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

    # --- Gmail -------------------------------------------------------------
    # "app_password" (simplest, nothing to register) or "oauth".
    mail_transport: str = "app_password"
    # App Password from https://myaccount.google.com/apppasswords
    gmail_app_password: str = ""
    # Only needed when MAIL_TRANSPORT=oauth.
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

    # --- Year to date ------------------------------------------------------
    # Month the financial year starts in, 1-12. House of Marketers' deal titles
    # put Oct/Nov 2026 in "FY 27", which points at an April start; set
    # FINANCIAL_YEAR_START_MONTH=1 for a calendar year.
    financial_year_start_month: int = 4
    # Show the won year-to-date chart in the email.
    show_ytd: bool = True
    # Show delivered-campaign gross margin beside each brand.
    show_profitability: bool = True
    # Supabase client names to point at a Pipedrive brand by hand, e.g.
    # "Match.com LLC = Match Group; Rocky Road Games = Rocky Road".
    client_aliases: str = ""

    # --- Web ---------------------------------------------------------------
    port: int = 8000
    admin_token: str = ""

    @field_validator("mail_to", "mail_cc", mode="before")
    @classmethod
    def _parse_emails(cls, value):
        return _split_emails(value)

    def financial_year_start(self, on: "date") -> "date":
        """First day of the financial year that ``on`` falls in."""
        from datetime import date as _date

        month = min(12, max(1, self.financial_year_start_month))
        year = on.year if (on.month, on.day) >= (month, 1) else on.year - 1
        return _date(year, month, 1)

    @property
    def period_label(self) -> str:
        """"week" or "fortnight", taken from the cadence.

        Derived rather than configured so the email cannot claim one cadence
        while the scheduler runs another.
        """
        return "fortnight" if self.schedule_fortnightly else "week"

    def field_overrides(self) -> dict[str, str]:
        return {
            "deal_account_manager": self.pipedrive_field_account_manager,
            "org_industry": self.pipedrive_field_industry,
            "org_sub_industry": self.pipedrive_field_sub_industry,
            "org_website": self.pipedrive_field_org_website,
            "deal_campaign_start": self.pipedrive_field_campaign_start,
            "deal_campaign_end": self.pipedrive_field_campaign_end,
        }

    @property
    def pipedrive_base_url(self) -> str:
        return f"https://{self.pipedrive_domain}.pipedrive.com"

    def mail_configured(self) -> bool:
        if not self.mail_from:
            return False
        if self.mail_transport == "oauth":
            return bool(self.gmail_client_id and self.gmail_client_secret and self.gmail_refresh_token)
        return bool(self.gmail_app_password)

    def resolved_recipients(self) -> tuple[list[str], list[str]]:
        """Return (to, cc) after applying TEST_MODE redirection."""
        if self.test_mode:
            target = self.test_recipient or self.mail_from
            return ([target] if target else [], [])
        return (list(self.mail_to), list(self.mail_cc))


@lru_cache
def get_settings() -> Settings:
    return Settings()
