"""Fortnightly scheduling.

APScheduler's cron trigger fires weekly; `is_due` gates the off weeks so the
cadence is every second week measured from SCHEDULE_ANCHOR_DATE.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Settings

log = logging.getLogger(__name__)


def is_due(settings: Settings, today: date) -> bool:
    """True when `today` falls in a send week."""
    if not settings.schedule_fortnightly:
        return True
    try:
        anchor = date.fromisoformat(settings.schedule_anchor_date)
    except ValueError:
        log.warning("Invalid SCHEDULE_ANCHOR_DATE %r; sending every week",
                    settings.schedule_anchor_date)
        return True
    # Compare ISO week starts so a shifted weekday still lands in the right week.
    anchor_week = anchor.toordinal() - anchor.weekday()
    today_week = today.toordinal() - today.weekday()
    return ((today_week - anchor_week) // 7) % 2 == 0


def next_runs(settings: Settings, count: int = 3) -> list[str]:
    """Preview the next few firing dates that would actually send."""
    from .report import today_in

    if not settings.schedule_enabled:
        return []
    trigger = CronTrigger.from_crontab(settings.schedule_cron, timezone=settings.report_timezone)
    runs: list[str] = []
    previous = datetime.now(trigger.timezone)
    while len(runs) < count:
        nxt = trigger.get_next_fire_time(previous, previous)
        if nxt is None:
            break
        if is_due(settings, nxt.date()):
            runs.append(nxt.isoformat())
        previous = nxt
    return runs


def start(settings: Settings, job) -> BackgroundScheduler | None:
    if not settings.schedule_enabled:
        log.info("Scheduler disabled (SCHEDULE_ENABLED=false); trigger runs manually.")
        return None

    scheduler = BackgroundScheduler(timezone=settings.report_timezone)

    def guarded() -> None:
        from .report import today_in

        today = today_in(settings.report_timezone)
        if not is_due(settings, today):
            log.info("Skipping %s - off week in the fortnightly cadence", today)
            return
        job()

    scheduler.add_job(
        guarded,
        CronTrigger.from_crontab(settings.schedule_cron, timezone=settings.report_timezone),
        id="pipeline_report",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    log.info("Scheduler started: cron=%r tz=%s fortnightly=%s",
             settings.schedule_cron, settings.report_timezone, settings.schedule_fortnightly)
    return scheduler
