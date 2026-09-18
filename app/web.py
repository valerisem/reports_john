"""HTTP surface: health, previews and a manual trigger."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import report, scheduler
from .config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    settings = get_settings()
    _scheduler = scheduler.start(settings, lambda: report.run(settings))
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="HoM Pipeline Report", lifespan=lifespan)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Protects the endpoints that cost API calls or send mail."""
    settings = get_settings()
    if not settings.admin_token:
        return  # no token configured -> open, intended for local use
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "test_mode": settings.test_mode,
        "schedule_enabled": settings.schedule_enabled,
        "schedule_cron": settings.schedule_cron if settings.schedule_enabled else None,
        "fortnightly": settings.schedule_fortnightly,
        "next_runs": scheduler.next_runs(settings),
        "pipedrive_configured": bool(settings.pipedrive_api_token),
        "supabase_configured": bool(settings.supabase_url and settings.supabase_key),
        "mail_transport": settings.mail_transport,
        "mail_configured": settings.mail_configured(),
        "recipients": {"to": settings.mail_to, "cc": settings.mail_cc},
    }


def _artefacts(settings: Settings):
    return report.build(settings, report.collect(settings))


@app.get("/preview/email", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def preview_email() -> HTMLResponse:
    """The newsletter exactly as it will be sent, rendered in the browser."""
    settings = get_settings()
    return HTMLResponse(_artefacts(settings).html)


@app.get("/preview/excel", dependencies=[Depends(require_admin)])
def preview_excel() -> Response:
    settings = get_settings()
    artefacts = _artefacts(settings)
    return Response(
        content=artefacts.workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{artefacts.filename}"'},
    )


@app.get("/preview/data", dependencies=[Depends(require_admin)])
def preview_data() -> JSONResponse:
    """The numbers behind the report, for spot-checking against Pipedrive."""
    settings = get_settings()
    data = report.collect(settings)
    return JSONResponse(
        {
            "report_date": data.report_date.isoformat(),
            "rates": data.rates,
            "rates_are_live": data.rates_are_live,
            "open_deals": data.open_deal_count,
            "brands": len(data.brands),
            "new_brands": data.new_brand_count,
            "pipeline_gbp": round(data.pipeline_gbp),
            "weighted_gbp": round(data.weighted_gbp),
            "new_business_gbp": round(data.new_business_gbp),
            # Deals kept in the totals despite no organisation being linked in
            # Pipedrive: each is a data gap worth fixing at the source.
            "deals_missing_organisation": [
                {"title": d.title, "brand": d.brand, "stage": d.stage, "value_gbp": round(d.value_gbp)}
                for d in data.deals_missing_organisation
            ],
            "by_stage": data.by_stage(),
            "by_owner": data.by_owner(),
            "by_manager": data.by_manager(),
            "by_pod": data.by_pod(),
            "team_directory_loaded": data.directory.loaded,
            "pods": [
                {"lead": p.lead, "account_managers": p.account_managers}
                for p in data.directory.pods
            ],
            "by_industry": data.by_industry(),
        }
    )


@app.get("/preview/fields", dependencies=[Depends(require_admin)])
def preview_fields() -> JSONResponse:
    """Which Pipedrive fields exist and which ones the report matched.

    Use this when a column comes out blank: it shows every field name the
    account has, so a renamed field is obvious.
    """
    from .pipedrive import PipedriveClient

    settings = get_settings()
    with PipedriveClient(settings.pipedrive_api_token, settings.pipedrive_base_url) as client:
        catalogue = client.field_catalogue()
        matched = client.field_keys(settings.field_overrides())
        return JSONResponse({"matched": matched, "available": catalogue})


@app.get("/preview/brands", dependencies=[Depends(require_admin)])
def preview_brands() -> JSONResponse:
    """Which Pipedrive organisations cluster into the same brand.

    Duplicated records are why a returning client can be reported as new
    business: the won history sits on one record and the open deal on another.
    """
    from .brands import resolve_brands
    from .pipedrive import PipedriveClient

    settings = get_settings()
    with PipedriveClient(settings.pipedrive_api_token, settings.pipedrive_base_url) as client:
        orgs = {org["id"]: org for org in client.all_organizations()}
    by_org = resolve_brands(orgs)

    brands = {id(b): b for b in by_org.values()}.values()
    duplicated = sorted(
        (b for b in brands if b.is_duplicated), key=lambda b: (-len(b.org_ids), b.name.lower())
    )
    return JSONResponse(
        {
            "organisations": len(orgs),
            "brands": len(brands),
            "duplicated_brands": len(duplicated),
            "clusters": [
                {
                    "brand": b.name,
                    "key": b.key,
                    "org_ids": b.org_ids,
                    "names": b.names,
                    "domains": sorted(b.domains),
                    "won_deals_pooled": b.won_deals,
                    "status": "Existing client" if b.is_existing_client else "New business",
                }
                for b in duplicated
            ],
        }
    )


@app.post("/seed-brand-history", dependencies=[Depends(require_admin)])
def seed_brand_history() -> dict:
    """Mark every brand currently in the pipeline as already announced.

    Run once before the first live send, or John's first email presents the
    whole existing pipeline as new business.
    """
    return report.seed_brand_history(get_settings())


@app.get("/preview/new-brands", dependencies=[Depends(require_admin)])
def preview_new_brands() -> JSONResponse:
    """What the email would flag as new, without sending or recording it."""
    settings = get_settings()
    data = report.collect(settings)
    return JSONResponse(
        {
            "brand_history_loaded": data.history.loaded,
            "brands_already_reported": len(data.history.reported_keys),
            "new_business_share": round(data.new_business_share, 4),
            "new_this_report": [
                {"brand": b.name, "key": b.brand_key, "weighted_gbp": round(b.weighted_gbp)}
                for b in data.brands_new_this_report
            ],
        }
    )


@app.post("/run", dependencies=[Depends(require_admin)])
def run_now(dry_run: bool = Query(default=False, description="Build everything but do not send")) -> dict:
    """Build and send the report immediately, honouring TEST_MODE."""
    return report.run(get_settings(), dry_run=dry_run)
