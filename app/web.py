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
            "by_stage": data.by_stage(),
            "by_owner": data.by_owner(),
            "by_manager": data.by_manager(),
            "by_industry": data.by_industry(),
        }
    )


@app.post("/run", dependencies=[Depends(require_admin)])
def run_now(dry_run: bool = Query(default=False, description="Build everything but do not send")) -> dict:
    """Build and send the report immediately, honouring TEST_MODE."""
    return report.run(get_settings(), dry_run=dry_run)
