"""Orchestration: pull from Pipedrive, build the artefacts, send the email."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import (
    brand_history, campaign_finance, email_html, excel, fx, mailer, pipeline_trend, progress,
    team_directory, weekly_cache,
)
from .brands import resolve_brands
from .client_aliases import alias_map
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



def _collect_progress(settings: Settings, client: PipedriveClient, data: ReportData, *,
                      orgs: dict[int, dict], brands_by_org: dict, won_payload: list[dict],
                      stages_payload: list[dict]) -> progress.Progress | None:
    """The weekly picture: new-brand pipeline, win rate and big clients.

    Finished weeks come from the Supabase cache where saved; anything missing
    is rebuilt from Pipedrive's change logs and saved for next time. This is
    the most expendable part of a run, so nothing here is allowed to raise:
    a failure costs the section, not the report.
    """
    if not (settings.show_progress or settings.show_trend):
        return None
    try:
        report_date = data.report_date
        weeks_shown = max(1, settings.progress_weeks)
        history_weeks = max(progress.HISTORY_WEEKS, weeks_shown)
        probabilities = pipeline_trend.stage_probabilities(stages_payload)
        clock = progress.BrandClock(brands_by_org, orgs, won_payload)
        deals = client.deals_for_history(settings.pipedrive_pipeline_id)

        first_week = progress.week_endings(report_date, history_weeks)[0]
        cached = weekly_cache.load_weeks(settings.supabase_url, settings.supabase_key, first_week)
        weeks, fresh = progress.build_weeks(
            report_date=report_date, cached=cached, deals=deals,
            fetch_changelogs=lambda ids: client.changelogs(ids, workers=settings.trend_workers),
            probability_by_stage=probabilities, clock=clock, count=history_weeks,
        )
        weekly_cache.save_weeks(settings.supabase_url, settings.supabase_key, fresh)

        rates = data.rates
        window = settings.win_rate_weeks
        lost_since = progress.week_endings(report_date, weeks_shown)[0] - timedelta(weeks=window + 1)
        won = progress.closed_deals(won_payload, "won_time", rates, brands_by_org)
        lost = progress.closed_deals(
            client.lost_deals(lost_since, settings.pipedrive_pipeline_id),
            "lost_time", rates, brands_by_org,
        )
        result = progress.Progress(
            rates=rates,
            weeks=weeks,
            now=progress.now_point(report_date=report_date, deals=deals,
                                   probability_by_stage=probabilities, clock=clock),
            win_rates=[
                progress.win_rate(won, lost, week.week_ending, window)
                for week in weeks[-weeks_shown:]
            ] + [progress.win_rate(won, lost, report_date, window)],
            win_rate_weeks=window,
            clients=progress.big_clients(
                won, report_date,
                {b.brand_key: (b.open_deals, b.weighted_gbp) for b in data.brands if b.brand_key},
                settings.big_client_count,
            ),
            weeks_shown=weeks_shown,
            fresh_weeks=len(fresh),
        )
        if result.last_week is not None:
            result.flow = progress.build_flow(
                since=result.last_week.week_ending, report_date=report_date, deals=deals,
                won=won, lost=lost, clock=clock, rates=rates,
            )
        return result
    except Exception as exc:  # noqa: BLE001 - never worth a failed send
        log.warning("Weekly progress could not be built: %s", exc)
        return None


def collect(settings: Settings, report_date: date | None = None) -> ReportData:
    """Fetch everything the report needs from Pipedrive."""
    report_date = report_date or today_in(settings.report_timezone)
    rates, live = fx.get_rates(
        settings.fx_api_url, settings.fx_fallback_usd_gbp, settings.fx_fallback_eur_gbp
    )

    directory = team_directory.load_directory(
        settings.supabase_url, settings.supabase_key, report_date
    )
    history = brand_history.load_history(settings.supabase_url, settings.supabase_key)
    finance = (
        campaign_finance.load_campaign_finance(settings.supabase_url, settings.supabase_key)
        if settings.show_profitability
        else campaign_finance.CampaignFinanceSet()
    )

    with PipedriveClient(settings.pipedrive_api_token, settings.pipedrive_base_url) as client:
        pipeline_id = settings.pipedrive_pipeline_id
        deals = client.open_deals(pipeline_id)
        org_ids = {deal["org_id"] for deal in deals if deal.get("org_id")}
        person_ids = {deal["person_id"] for deal in deals if deal.get("person_id")}

        # Every organisation, not just those with open deals: a brand's won
        # history often sits on a duplicate record that has none, and clustering
        # cannot pool history it never sees.
        all_orgs = {org["id"]: org for org in client.all_organizations()}
        won_org_ids = {
            org_id for org_id, org in all_orgs.items()
            if (org.get("won_deals_count") or 0) > 0
        }

        # Campaign history reaches back past the open pipeline, so the deals
        # behind it are looked up by id rather than swept by status.
        campaign_deal_orgs = (
            client.deal_orgs(set(finance.by_deal)) if finance.loaded else {}
        )
        stages_payload = client.stages(pipeline_id)
        field_keys = client.field_keys(settings.field_overrides())
        # Resolved once here so the weekly history sorts deals into brands
        # exactly as the report does.
        brands_by_org = resolve_brands(
            all_orgs,
            {org_id: 1 for org_id in won_org_ids},
            website_key=field_keys.get("org_website"),
        )
        fy_start = settings.financial_year_start(report_date)
        # The big-client table compares this year with last, so with the
        # weekly section on, wins are read two years back.
        won_since = fy_start
        if settings.show_progress:
            won_since = min(fy_start, pipeline_trend.months_before(report_date, 24))
        want_won = settings.show_ytd or settings.show_progress
        won_payload = client.won_deals(won_since, pipeline_id) if want_won else []

        data = build_report(
            report_date=report_date,
            rates=rates,
            rates_are_live=live,
            stages_payload=stages_payload,
            deals_payload=deals,
            orgs=all_orgs,
            persons=client.persons(person_ids),
            org_contacts=client.persons_by_org(org_ids),
            users=client.users(),
            won_org_ids=won_org_ids,
            field_keys=field_keys,
            directory=directory,
            brands_by_org=brands_by_org,
            history=history,
            won_deals_payload=won_payload if settings.show_ytd else [],
            financial_year_start=fy_start,
            finance=finance,
            campaign_deal_orgs=campaign_deal_orgs,
            client_aliases=alias_map(settings.client_aliases),
        )
        data.progress = _collect_progress(
            settings, client, data, orgs=all_orgs, brands_by_org=brands_by_org,
            won_payload=won_payload, stages_payload=stages_payload,
        )
        if data.progress is not None and settings.show_trend:
            data.trend = progress.trend_from_weeks(report_date, data.progress.weeks, rates)
        if not settings.show_progress:
            data.progress = None
        return data


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
        new_brand_min=settings.new_brand_min,
        period_label=settings.period_label,
        new_brand_fallback_count=settings.new_brand_fallback_count,
        header_image_url=settings.header_image_url,
        footer_image_url=settings.footer_image_url,
        logo_url=settings.logo_url,
        owner_photo_url_template=settings.owner_photo_url_template,
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


def _record_brands(settings: Settings, data: ReportData) -> int | None:
    """Mark this report's brands as announced.

    Only after a live send: a test send must never burn a brand John has not
    actually seen. A failure here is logged, not raised - the email has gone.
    """
    if settings.test_mode:
        return None
    try:
        with brand_history.BrandHistoryStore(settings.supabase_url, settings.supabase_key) as store:
            return store.record(data.brand_records(), data.report_date)
    except Exception as exc:  # noqa: BLE001
        log.error("Could not record reported brands (%s); they may be announced again", exc)
        return None


def seed_brand_history(settings: Settings) -> dict:
    """Mark everything currently in the pipeline as already announced.

    Run once before going live, otherwise the first real email presents every
    existing new-business brand as fresh news.
    """
    data = collect(settings)
    with brand_history.BrandHistoryStore(settings.supabase_url, settings.supabase_key) as store:
        inserted = store.record(data.brand_records(), data.report_date)
    return {"brands_in_pipeline": len(data.brands), "newly_recorded": inserted}


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
        "brand_history_loaded": data.history.loaded,
        "new_this_report": [b.name for b in data.brands_new_this_report],
        "new_business_share": round(data.new_business_share, 4),
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
    summary["recorded_brands"] = _record_brands(settings, data)
    log.info("Report sent: %s", summary)
    return summary
