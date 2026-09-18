"""What the report has already told John about.

The email flags a brand as new business only the first time it appears. Without
a record of what has been sent, the same names would be announced fortnight
after fortnight. Pipedrive cannot answer this — it knows a brand is new
business, not whether we have mentioned it — so it lives in Supabase.

Writes happen only after a live send. A test send never marks a brand as
announced, so testing cannot silently burn a brand John has not seen.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import httpx

log = logging.getLogger(__name__)

TABLE = "report_brand_history"


@dataclass
class BrandHistory:
    reported_keys: set[str] = field(default_factory=set)
    loaded: bool = False

    def is_new(self, brand_key: str) -> bool:
        """True when this brand has never gone out in a report.

        With no history loaded nothing is called new, so a Supabase outage
        produces a quieter email rather than re-announcing every brand.
        """
        return self.loaded and brand_key not in self.reported_keys


class BrandHistoryStore:
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

    def __enter__(self) -> "BrandHistoryStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def load(self) -> BrandHistory:
        response = self._client.get(f"/{TABLE}", params={"select": "brand_key", "limit": 20000})
        if response.status_code >= 400:
            raise RuntimeError(f"GET {TABLE} -> {response.status_code}: {response.text[:300]}")
        return BrandHistory(
            reported_keys={row["brand_key"] for row in response.json() if row.get("brand_key")},
            loaded=True,
        )

    def record(self, brands: list[dict], on: date) -> int:
        """Insert brands not already recorded. Existing keys are left alone."""
        if not brands:
            return 0
        rows = [
            {
                "brand_key": b["brand_key"],
                "brand_name": b["brand_name"],
                "first_reported_on": on.isoformat(),
                "first_reported_status": b.get("status"),
                "pd_org_ids": b.get("org_ids") or [],
            }
            for b in brands
        ]
        response = self._client.post(
            f"/{TABLE}",
            json=rows,
            # Ignore keys already present so a re-run never rewrites the date a
            # brand was first announced.
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
            params={"on_conflict": "brand_key"},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"POST {TABLE} -> {response.status_code}: {response.text[:300]}")
        return len(response.json() or [])


def load_history(url: str, api_key: str) -> BrandHistory:
    """Best effort: an unreachable store must not stop the report going out."""
    if not url or not api_key:
        log.warning("Supabase not configured; brand history unavailable")
        return BrandHistory(loaded=False)
    try:
        with BrandHistoryStore(url, api_key) as store:
            history = store.load()
        log.info("Brand history: %d brands already reported", len(history.reported_keys))
        return history
    except Exception as exc:  # noqa: BLE001
        log.warning("Brand history unavailable (%s); nothing will be flagged new", exc)
        return BrandHistory(loaded=False)
