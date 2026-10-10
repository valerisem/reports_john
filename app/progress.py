"""Week by week: is the business moving the right way?

Three questions John asks of every report:

* Is the pipeline of new brands growing, in number and in value?
* Is the win rate holding up?
* Are the biggest clients committing to further programmes?

The pipeline series is rebuilt from Pipedrive's change logs (see
``pipeline_trend``) one finished week at a time, and each finished week is
saved (see ``weekly_cache``) so it is only ever worked out once. Win rate and
the big-client table come straight from won and lost deals, which are a few
paged requests and need no cache.

A week ends on Sunday. "Now" is the live position on the report date.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from .brands import Brand
from .model import brand_from_deal_title
from .pipeline_trend import (
    Snapshot, _as_datetime, _as_float, end_of, is_test_deal, months_before,
    snapshot_dates, tally_at,
)
from .weekly_cache import WeekPoint

log = logging.getLogger(__name__)

# Enough finished weeks to reach the workbook's "1 year ago" row.
HISTORY_WEEKS = 54


def week_endings(report_date: date, count: int) -> list[date]:
    """The last ``count`` finished weeks before the report, oldest first.

    A week is finished once its Sunday is over, so a report run on a Sunday
    still treats the Sunday before as the latest finished week.
    """
    back = (report_date.weekday() + 1) % 7 or 7
    last = report_date - timedelta(days=back)
    return [last - timedelta(weeks=n) for n in range(count - 1, -1, -1)]


def _org_id(deal: dict) -> int | None:
    """/v1 deals carry the organisation as an object, /v2 as a bare id."""
    org = deal.get("org_id")
    if isinstance(org, dict):
        org = org.get("value")
    return org if isinstance(org, int) else None


class BrandClock:
    """Whether a deal belonged to a new brand at a given moment.

    A brand stops being new business the moment its first deal is won. Wins
    older than the fetched window still count: an organisation whose
    won-deal count is higher than the wins we can see won something earlier,
    so it has been a client throughout.
    """

    def __init__(self, brands_by_org: dict[int, Brand], orgs: dict[int, dict],
                 won_deals: list[dict]):
        self._brands = brands_by_org
        self._first_win: dict[str, datetime] = {}
        seen: dict[str, int] = {}
        for deal in won_deals:
            brand = brands_by_org.get(_org_id(deal))
            won_at = _as_datetime(deal.get("won_time"))
            if brand is None or won_at is None:
                continue
            seen[brand.key] = seen.get(brand.key, 0) + 1
            if brand.key not in self._first_win or won_at < self._first_win[brand.key]:
                self._first_win[brand.key] = won_at
        self._client_throughout: set[str] = set()
        for brand in {b.key: b for b in brands_by_org.values()}.values():
            total = sum(int((orgs.get(o) or {}).get("won_deals_count") or 0) for o in brand.org_ids)
            if total > seen.get(brand.key, 0):
                self._client_throughout.add(brand.key)

    def is_new(self, deal: dict, moment: datetime) -> bool:
        brand = self._brands.get(_org_id(deal))
        if brand is None:
            return True  # no organisation linked: nobody has won anything with it
        if brand.key in self._client_throughout:
            return False
        first = self._first_win.get(brand.key)
        return first is None or first >= moment


def _updated_after(deal: dict, moment: datetime) -> bool:
    updated = _as_datetime(deal.get("update_time"))
    return updated is None or updated > moment


def build_weeks(
    *,
    report_date: date,
    cached: dict[date, WeekPoint],
    deals: list[dict],
    fetch_changelogs: Callable[[list[int]], dict[int, list[dict]]],
    probability_by_stage: dict[int, float],
    clock: BrandClock,
    count: int = HISTORY_WEEKS,
) -> tuple[list[WeekPoint], list[WeekPoint]]:
    """Every finished week, oldest first, and which of them were new this run.

    Only weeks missing from the cache are worked out, and only deals edited
    since the earliest missing week need their change log: a deal untouched
    since then still holds the values it had that week.
    """
    weeks = week_endings(report_date, count)
    missing = [w for w in weeks if w not in cached]
    fresh: list[WeekPoint] = []
    if missing:
        since = end_of(missing[0])
        stale = [d["id"] for d in deals if d.get("id") is not None and _updated_after(d, since)]
        log.info("Rebuilding %d weeks; reading change logs for %d of %d deals",
                 len(missing), len(stale), len(deals))
        changelogs = fetch_changelogs(stale) if stale else {}
        for week in missing:
            moment = end_of(week)
            common = dict(moment=moment, deals=deals, changelogs=changelogs,
                          probability_by_stage=probability_by_stage)
            fresh.append(WeekPoint(week, tally_at(**common), tally_at(**common, include=clock.is_new)))
    by_week = {**{w: cached[w] for w in weeks if w in cached}, **{p.week_ending: p for p in fresh}}
    return [by_week[w] for w in weeks], fresh


def now_point(*, report_date: date, deals: list[dict], probability_by_stage: dict[int, float],
              clock: BrandClock) -> WeekPoint:
    """The live pipeline: every deal at its current values."""
    common = dict(moment=end_of(report_date), deals=deals, changelogs={},
                  probability_by_stage=probability_by_stage)
    return WeekPoint(report_date, tally_at(**common), tally_at(**common, include=clock.is_new))


def trend_from_weeks(report_date: date, weeks: list[WeekPoint],
                     rates: dict[str, float]) -> list[Snapshot]:
    """The workbook's year / six / three months ago rows, read off the weeks."""
    out: list[Snapshot] = []
    for label, target in snapshot_dates(report_date):
        before = [w for w in weeks if w.week_ending <= target]
        if not before:
            continue
        week = before[-1]
        out.append(Snapshot(
            label=f"{label}  (w/e {week.week_ending.strftime('%-d %b %Y')})",
            on=week.week_ending,
            open_deals=week.all.deals,
            value_gbp=week.all.value_gbp(rates),
            weighted_gbp=week.all.weighted_gbp(rates),
        ))
    return out


# -- closed deals ----------------------------------------------------------
@dataclass
class Closed:
    deal: dict
    brand_key: str
    brand: str
    on: datetime
    value_gbp: float


def closed_deals(payload: list[dict], time_key: str, rates: dict[str, float],
                 brands_by_org: dict[int, Brand]) -> list[Closed]:
    out = []
    for deal in payload:
        if is_test_deal(deal.get("title")):
            continue
        on = _as_datetime(deal.get(time_key))
        if on is None:
            continue
        brand = brands_by_org.get(_org_id(deal))
        name = brand.name if brand else brand_from_deal_title(deal.get("title"))
        currency = str(deal.get("currency") or "GBP").upper()
        out.append(Closed(
            deal=deal,
            brand_key=brand.key if brand else f"title:{name.lower()}",
            brand=name,
            on=on,
            value_gbp=_as_float(deal.get("value")) * rates.get(currency, 1.0),
        ))
    return out


@dataclass
class WinRate:
    on: date
    rate: float | None  # won value over won + lost value, 0..1
    won_gbp: float
    lost_gbp: float


def win_rate(won: list[Closed], lost: list[Closed], on: date, weeks: int) -> WinRate:
    """Share of closed value that was won, over the ``weeks`` up to ``on``."""
    end = end_of(on)
    start = end - timedelta(weeks=weeks)
    won_gbp = sum(c.value_gbp for c in won if start < c.on <= end)
    lost_gbp = sum(c.value_gbp for c in lost if start < c.on <= end)
    total = won_gbp + lost_gbp
    return WinRate(on, (won_gbp / total) if total else None, won_gbp, lost_gbp)


# -- big clients -----------------------------------------------------------
@dataclass
class BigClient:
    name: str
    won_gbp: float  # last 12 months
    programmes: int  # deals won in the last 12 months
    programmes_before: int  # deals won in the 12 months before that
    open_deals: int
    open_weighted_gbp: float


def big_clients(won: list[Closed], report_date: date, open_by_brand: dict[str, tuple[int, float]],
                limit: int) -> list[BigClient]:
    """The clients that won us the most over the last year, biggest first."""
    year_ago = end_of(months_before(report_date, 12))
    two_years_ago = end_of(months_before(report_date, 24))
    end = end_of(report_date)
    rows: dict[str, BigClient] = {}
    for c in won:
        if c.on > end or c.on <= two_years_ago:
            continue
        row = rows.setdefault(c.brand_key, BigClient(c.brand, 0.0, 0, 0, 0, 0.0))
        if c.on > year_ago:
            row.won_gbp += c.value_gbp
            row.programmes += 1
        else:
            row.programmes_before += 1
    ranked = sorted((r for r in rows.values() if r.won_gbp > 0),
                    key=lambda r: (-r.won_gbp, r.name.lower()))[:limit]
    for row in ranked:
        row.open_deals, row.open_weighted_gbp = open_by_brand.get(row.name, (0, 0.0))
    return ranked


# -- the whole picture -----------------------------------------------------
@dataclass
class Flow:
    """New-brand deals that came in and went out since the comparison week."""
    since: date
    added: int = 0
    added_gbp: float = 0.0
    won: int = 0
    won_gbp: float = 0.0
    lost: int = 0
    lost_gbp: float = 0.0


@dataclass
class Progress:
    rates: dict[str, float]
    weeks: list[WeekPoint]  # finished weeks, oldest first
    now: WeekPoint
    win_rates: list[WinRate]  # one per shown week, then now
    win_rate_weeks: int
    clients: list[BigClient]
    flow: Flow | None = None
    weeks_shown: int = 26
    fresh_weeks: int = 0  # worked out this run rather than read from the cache
    notes: list[str] = field(default_factory=list)

    @property
    def shown(self) -> list[WeekPoint]:
        return self.weeks[-self.weeks_shown:]

    @property
    def last_week(self) -> WeekPoint | None:
        """The finished week at least seven days before the report."""
        cutoff = self.now.week_ending - timedelta(days=7)
        earlier = [w for w in self.weeks if w.week_ending <= cutoff]
        return earlier[-1] if earlier else None


def build_flow(*, since: date, report_date: date, deals: list[dict], won: list[Closed],
               lost: list[Closed], clock: BrandClock, rates: dict[str, float]) -> Flow:
    flow = Flow(since=since)
    start, end = end_of(since), end_of(report_date)
    for deal in deals:
        if is_test_deal(deal.get("title")):
            continue
        added = _as_datetime(deal.get("add_time"))
        if added is None or not (start < added <= end) or not clock.is_new(deal, added):
            continue
        currency = str(deal.get("currency") or "GBP").upper()
        flow.added += 1
        flow.added_gbp += _as_float(deal.get("value")) * rates.get(currency, 1.0)
    for c in won:
        if start < c.on <= end and clock.is_new(c.deal, c.on):
            flow.won += 1
            flow.won_gbp += c.value_gbp
    for c in lost:
        if start < c.on <= end and clock.is_new(c.deal, c.on):
            flow.lost += 1
            flow.lost_gbp += c.value_gbp
    return flow
