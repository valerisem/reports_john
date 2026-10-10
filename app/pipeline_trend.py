"""Rebuild what the pipeline looked like on a past date.

Pipedrive keeps no history of its own, but it logs every field change, so a
snapshot can be reconstructed: for each field, the earliest change recorded
after the snapshot date carries the value as it was *before* that change, and
that is the value on the day. Where a field never changed after the date, the
deal's current value already is the value on the day. One rule covers initial
values, reopened deals, edited amounts and deletions alike.

Deleted deals are included deliberately. A deal deleted in September was still
open in July, and dropping it would understate every earlier snapshot.

Amounts are kept per currency and converted at the run's own rates, so the
trend shows the pipeline moving rather than the exchange rate moving. Stage
probabilities are applied when a week is first worked out; a saved week keeps
the probabilities of that day.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable

log = logging.getLogger(__name__)

# Fields whose value at a past date the snapshot needs.
TRACKED = ("status", "stage_id", "value", "currency")
OPEN = "open"

# Deals somebody created to try the form out. They are real rows in Pipedrive
# and would otherwise sit in the history forever.
TEST_TITLES = {"test", "testvaleria", "jjskjskskskss", "testtest", "test deal"}


@dataclass
class Snapshot:
    label: str
    on: date
    open_deals: int
    value_gbp: float
    weighted_gbp: float


def _as_datetime(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for parse in (datetime.fromisoformat,):
        try:
            parsed = parse(text)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def is_test_deal(title) -> bool:
    cleaned = "".join(str(title or "").lower().split())
    return cleaned in TEST_TITLES


def value_at(deal: dict, changes: list[dict], field: str, moment: datetime):
    """The deal's value for one field as it stood at ``moment``.

    The earliest change logged after the moment holds the previous value in
    ``old_value``; with no such change the current value has stood since.
    """
    after = [
        change for change in changes
        if change.get("field_key") == field
        and (_as_datetime(change.get("time")) or datetime.max.replace(tzinfo=timezone.utc)) > moment
    ]
    if not after:
        return deal.get(field)
    earliest = min(after, key=lambda c: _as_datetime(c.get("time")) or datetime.max.replace(tzinfo=timezone.utc))
    return earliest.get("old_value")


@dataclass
class Tally:
    """Open pipeline at one moment, kept per currency.

    Amounts stay in their own currency so a figure saved months ago can be
    valued at today's rates, and an old week never moves because sterling did.
    """
    deals: int = 0
    value: dict[str, float] = field(default_factory=dict)
    weighted: dict[str, float] = field(default_factory=dict)

    def add(self, currency: str, amount: float, probability: float) -> None:
        self.deals += 1
        self.value[currency] = self.value.get(currency, 0.0) + amount
        self.weighted[currency] = self.weighted.get(currency, 0.0) + amount * probability

    def value_gbp(self, rates: dict[str, float]) -> float:
        return in_sterling(self.value, rates)

    def weighted_gbp(self, rates: dict[str, float]) -> float:
        return in_sterling(self.weighted, rates)


def in_sterling(amounts: dict[str, float], rates: dict[str, float]) -> float:
    return sum(amount * rates.get(currency, 1.0) for currency, amount in amounts.items())


def end_of(on: date) -> datetime:
    return datetime.combine(on, datetime.max.time()).replace(tzinfo=timezone.utc)


def tally_at(
    *,
    moment: datetime,
    deals: list[dict],
    changelogs: dict[int, list[dict]],
    probability_by_stage: dict[int, float],
    include: Callable[[dict, datetime], bool] | None = None,
) -> Tally:
    """Every deal that was open at ``moment``, optionally narrowed by ``include``."""
    tally = Tally()
    for deal in deals:
        if is_test_deal(deal.get("title")):
            continue
        added = _as_datetime(deal.get("add_time"))
        if added is None or added > moment:
            continue  # the deal did not exist yet
        changes = changelogs.get(deal.get("id"), [])
        if str(value_at(deal, changes, "status", moment) or "").lower() != OPEN:
            continue
        if include is not None and not include(deal, moment):
            continue

        currency = str(value_at(deal, changes, "currency", moment) or "GBP").upper()
        amount = _as_float(value_at(deal, changes, "value", moment))
        try:
            stage_id = int(value_at(deal, changes, "stage_id", moment))
        except (TypeError, ValueError):
            stage_id = None
        tally.add(currency, amount, probability_by_stage.get(stage_id, 0.0))
    return tally


def snapshot_at(
    *,
    label: str,
    on: date,
    deals: list[dict],
    changelogs: dict[int, list[dict]],
    rates: dict[str, float],
    probability_by_stage: dict[int, float],
) -> Snapshot:
    """Totals for every deal that was open at the end of ``on``."""
    tally = tally_at(moment=end_of(on), deals=deals, changelogs=changelogs,
                     probability_by_stage=probability_by_stage)
    return Snapshot(label=label, on=on, open_deals=tally.deals,
                    value_gbp=tally.value_gbp(rates), weighted_gbp=tally.weighted_gbp(rates))


def months_before(on: date, months: int) -> date:
    """The same day-of-month ``months`` earlier, clamped to a real date."""
    year, month = on.year, on.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = on.day
    while day > 1:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, 1)


def snapshot_dates(report_date: date) -> list[tuple[str, date]]:
    """One year, six months and three months before the report."""
    return [
        ("1 year ago", months_before(report_date, 12)),
        ("6 months ago", months_before(report_date, 6)),
        ("3 months ago", months_before(report_date, 3)),
    ]


def stage_probabilities(stages_payload: list[dict]) -> dict[int, float]:
    return {
        stage["id"]: float(stage.get("deal_probability") or 0) / 100.0
        for stage in stages_payload
        if stage.get("id") is not None
    }
