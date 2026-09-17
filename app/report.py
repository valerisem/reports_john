"""Orchestration: pull from Pipedrive, build the artefacts, send the email."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import email_html, excel, fx, mailer, team_directory
from .config import Settings
from .model import ReportData, build_report
from .pipedrive import PipedriveClient

log = logging.getLogger(__name__)


@dataclass
class Artefacts:
    data: ReportData
    html: str
    text: str
    workbook: bytes
    filename: str
    subject: str
    to: list[str]
    cc: list[str]
    test_mode: bool


def today_in(timezone: str) -> date:
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except Exception:  # noqa: BLE001 - bad tz should not break the report
        log.warning("Unknown timezone %r; falling back to UTC", timezone)
        return datetime.utcnow().date()


def collect(settings: Settings, report_date: date | None = None) -> ReportData:
    """Fetch everything the report needs from Pipedrive."""
    report_date = report_date or today_in(settings.report_timezone)
    rates, live = fx.get_rates(
        settings.fx_api_url, settings.fx_fallback_usd_gbp, settings.fx_fallback_eur_gbp
    )

    directory = team_directory.load_directory(
        settings.supabase_url, settings.supabase_key, report_date
    )

    with PipedriveClient(settings.pipedrive_api_token, settings.pipedrive_base_url) as client:
        pipeline_id = settings.pipedrive_pipeline_id
        deals = client.open_deals(pipeline_id)
        org_ids = {deal["org_id"] for deal in deals if deal.get("org_id")}
        person_ids = {deal["person_id"] for deal in deals if deal.get("person_id")}

        return build_report(
            report_date=report_date,
            rates=rates,
            rates_are_live=live,
            stages_payload=client.stages(pipeline_id),
            deals_payload=deals,
            orgs=client.organizations(org_ids),
            persons=client.persons(person_ids),
            org_contacts=client.persons_by_org(org_ids),
            users=client.users(),
            won_org_ids=client.won_deal_org_ids(org_ids),
            field_keys=client.field_keys(settings.field_overrides()),
            directory=directory,
        )


def build(settings: Settings, data: ReportData) -> Artefacts:
    to, cc = settings.resolved_recipients()
    banner = None
    if settings.test_mode:
        banner = {"to": ", ".join(settings.mail_to), "cc": ", ".join(settings.mail_cc)}

    html = email_html.render_email(
        data,
        title=settings.report_title,
        greeting_name=settings.report_greeting_name,
        sender_name=settings.report_sender_name,
        test_banner=banner,
        header_image_url=settings.header_image_url,
        logo_url=settings.logo_url,
    )
    text = email_html.plain_text_fallback(
        data, settings.report_greeting_name, settings.report_sender_name
    )
    workbook = excel.build_workbook(data).getvalue()
    filename = f"HoM_Pipeline_Report_{data.report_date.isoformat()}.xlsx"
    subject = settings.mail_subject_template.format(
        date=data.report_date.strftime("%-d %B %Y"),
        short_date=data.report_date.isoformat(),
    )
    if settings.test_mode:
        subject = f"[TEST] {subject}"

    return Artefacts(
        data=data,
        html=html,
        text=text,
        workbook=workbook,
        filename=filename,
        subject=subject,
        to=to,
        cc=cc,
        test_mode=settings.test_mode,
    )


def send(settings: Settings, artefacts: Artefacts) -> str:
    if not artefacts.to:
        raise mailer.MailError(
            "No recipients resolved. In test mode set TEST_RECIPIENT (or MAIL_FROM); "
            "for a live send set MAIL_TO."
        )
    message = mailer.build_message(
        sender=settings.mail_from,
        sender_name=settings.mail_from_name,
        to=artefacts.to,
        cc=artefacts.cc,
        subject=artefacts.subject,
        html_body=artefacts.html,
        text_body=artefacts.text,
        attachment=(artefacts.filename, artefacts.workbook),
    )
    return mailer.send(
        transport=settings.mail_transport,
        message=message,
        username=settings.mail_from,
        app_password=settings.gmail_app_password,
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        refresh_token=settings.gmail_refresh_token,
    )


def run(settings: Settings, *, dry_run: bool = False) -> dict:
    """Full cycle. Returns a small summary for logs and the HTTP response."""
    data = collect(settings)
    artefacts = build(settings, data)
    summary = {
        "report_date": data.report_date.isoformat(),
        "open_deals": data.open_deal_count,
        "brands": len(data.brands),
        "pipeline_gbp": round(data.pipeline_gbp),
        "weighted_gbp": round(data.weighted_gbp),
        "rates": data.rates,
        "rates_are_live": data.rates_are_live,
        "team_directory_loaded": data.directory.loaded,
        "test_mode": artefacts.test_mode,
        "to": artefacts.to,
        "cc": artefacts.cc,
        "subject": artefacts.subject,
        "attachment": artefacts.filename,
    }
    if dry_run:
        summary["sent"] = False
        return summary
    summary["message_id"] = send(settings, artefacts)
    summary["sent"] = True
    log.info("Report sent: %s", summary)
    return summary
