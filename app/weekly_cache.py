"""Finished weeks of pipeline history, kept so they are only rebuilt once.

Rebuilding a past week reads every deal's change log from Pipedrive - one
request per deal - so a finished week is saved the first time it is worked out
and read back on every later run. A finished week cannot change afterwards:
an edit made today is logged today, which is after the week ended.

Amounts are stored per currency (see ``pipeline_trend.Tally``) and converted at
the run's own rates, so the whole series is valued alike.

The table is ``report_weekly_pipeline``; ``sql/report_weekly_pipeline.sql``
creates it. Without it the report still goes out - every run simply rebuilds
the weeks from Pipedrive, as it did before the cache existed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import httpx

from .pipeline_trend import Tally

log = logging.getLogger(__name__)

TABLE = "report_weekly_pipeline"


@dataclass
class WeekPoint:
    """Open pipeline at the end of one week: everything, and new brands only."""
    week_ending: date
    all: Tally
    new: Tally

    def to_row(self) -> dict:
        return {
            "week_ending": self.week_ending.isoformat(),
            "all_deals": self.all.deals,
            "all_value": self.all.value,
            "all_weighted": self.all.weighted,
            "new_deals": self.new.deals,
            "new_value": self.new.value,
            "new_weighted": self.new.weighted,
        }

    @classmethod
    def from_row(cls, row: dict) -> "WeekPoint":
        def amounts(key: str) -> dict[str, float]:
            return {str(k): float(v) for k, v in (row.get(key) or {}).items()}

        return cls(
            week_ending=date.fromisoformat(str(row["week_ending"])[:10]),
            all=Tally(int(row.get("all_deals") or 0), amounts("all_value"), amounts("all_weighted")),
            new=Tally(int(row.get("new_deals") or 0), amounts("new_value"), amounts("new_weighted")),
        )


class WeeklyCache:
    def __init__(self, url: str, api_key: str, timeout: float = 30.0):
        self._client = httpx.Client(
            base_url=url.rstrip("/") + "/rest/v1",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WeeklyCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def load(self, since: date) -> dict[date, WeekPoint]:
        response = self._client.get(
            f"/{TABLE}",
            params={"select": "*", "week_ending": f"gte.{since.isoformat()}", "limit": 1000},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"GET {TABLE} -> {response.status_code}: {response.text[:300]}")
        points = [WeekPoint.from_row(row) for row in response.json()]
        return {point.week_ending: point for point in points}

    def save(self, points: list[WeekPoint]) -> None:
        if not points:
            return
        response = self._client.post(
            f"/{TABLE}",
            json=[point.to_row() for point in points],
            # A week already saved is never rewritten.
            headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
            params={"on_conflict": "week_ending"},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"POST {TABLE} -> {response.status_code}: {response.text[:300]}")


def load_weeks(url: str, api_key: str, since: date) -> dict[date, WeekPoint]:
    """Best effort: an unreachable cache means rebuilding, not failing."""
    if not url or not api_key:
        return {}
    try:
        with WeeklyCache(url, api_key) as cache:
            weeks = cache.load(since)
        log.info("Weekly cache: %d finished weeks already saved", len(weeks))
        return weeks
    except Exception as exc:  # noqa: BLE001
        log.warning("Weekly cache unavailable (%s); rebuilding every week from Pipedrive", exc)
        return {}


def save_weeks(url: str, api_key: str, points: list[WeekPoint]) -> None:
    if not url or not api_key or not points:
        return
    try:
        with WeeklyCache(url, api_key) as cache:
            cache.save(points)
        log.info("Weekly cache: saved %d finished weeks", len(points))
    except Exception as exc:  # noqa: BLE001
        log.warning("Weekly cache could not be saved (%s); next run rebuilds them", exc)
