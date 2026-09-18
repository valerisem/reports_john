"""Thin Pipedrive REST client.

Custom fields are resolved by their human-readable name at runtime (via
``/dealFields`` and ``/organizationFields``) rather than by hard-coded hash
keys, so the app keeps working if the CRM is rebuilt or copied.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)

# Field labels we look for in Pipedrive, most specific first.
# Keep every entry a distinctive phrase: short aliases match far too much.
# ("am" once matched "Product amount" and filled the Account Manager column
# with product totals.)
DEAL_ACCOUNT_MANAGER_LABELS = ("account manager", "account mgr", "am owner")
ORG_INDUSTRY_LABELS = ("wide niche", "industry")
ORG_SUB_INDUSTRY_LABELS = ("narrow niche", "sub-industry", "sub industry", "subindustry")

# Custom fields have a 40-character hex key; built-ins have readable ones.
CUSTOM_KEY = re.compile(r"^[0-9a-f]{40}$")
MIN_FUZZY_LABEL = 6


class PipedriveError(RuntimeError):
    pass


class PipedriveClient:
    def __init__(self, api_token: str, base_url: str = "https://api.pipedrive.com", timeout: float = 30.0):
        if not api_token:
            raise PipedriveError("PIPEDRIVE_API_TOKEN is not set")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"x-api-token": api_token, "Accept": "application/json"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PipedriveClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low level ---------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        response = self._client.get(path, params=params or {})
        if response.status_code >= 400:
            raise PipedriveError(f"GET {path} -> {response.status_code}: {response.text[:400]}")
        payload = response.json()
        if not payload.get("success", True):
            raise PipedriveError(f"GET {path} returned success=false: {payload!r}")
        return payload

    def _paginate_v2(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        """Cursor pagination used by the /api/v2 endpoints."""
        cursor: str | None = None
        while True:
            page_params = dict(params or {}, limit=500)
            if cursor:
                page_params["cursor"] = cursor
            payload = self._get(path, page_params)
            yield from payload.get("data") or []
            cursor = (payload.get("additional_data") or {}).get("next_cursor")
            if not cursor:
                return

    def _paginate_v1(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        """Offset pagination used by the legacy /v1 endpoints."""
        start = 0
        while True:
            payload = self._get(path, dict(params or {}, start=start, limit=500))
            items = payload.get("data") or []
            yield from items
            pagination = (payload.get("additional_data") or {}).get("pagination") or {}
            if not pagination.get("more_items_in_collection"):
                return
            start = pagination.get("next_start", start + len(items))
            if not items:
                return

    # -- entities ----------------------------------------------------------
    def open_deals(self, pipeline_id: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"status": "open"}
        if pipeline_id:
            params["pipeline_id"] = pipeline_id
        return list(self._paginate_v2("/api/v2/deals", params))

    def won_deal_org_ids(self, org_ids: set[int]) -> set[int]:
        """Orgs with at least one won deal -> 'Existing client'.

        Read from each organisation's own ``won_deals_count`` rather than by
        sweeping won deals: /api/v2/deals hides archived deals, and Pipedrive
        archives old won ones, so long-standing clients were being counted as
        new business.
        """
        won: set[int] = set()
        ordered = sorted(org_ids)
        for i in range(0, len(ordered), 100):
            batch = ordered[i : i + 100]
            payload = self._get(
                "/api/v2/organizations",
                {
                    "ids": ",".join(str(x) for x in batch),
                    "include_fields": "won_deals_count",
                    "limit": 500,
                },
            )
            for org in payload.get("data") or []:
                if (org.get("won_deals_count") or 0) > 0:
                    won.add(org["id"])
        return won

    def organizations(self, ids: set[int]) -> dict[int, dict]:
        """Fetch organizations by id, in batches of 100."""
        result: dict[int, dict] = {}
        ordered = sorted(ids)
        for i in range(0, len(ordered), 100):
            batch = ordered[i : i + 100]
            payload = self._get(
                "/api/v2/organizations",
                {"ids": ",".join(str(x) for x in batch), "include_option_labels": "true", "limit": 500},
            )
            for org in payload.get("data") or []:
                result[org["id"]] = org
        return result

    def persons(self, ids: set[int]) -> dict[int, dict]:
        result: dict[int, dict] = {}
        ordered = sorted(ids)
        for i in range(0, len(ordered), 100):
            batch = ordered[i : i + 100]
            payload = self._get(
                "/api/v2/persons",
                {"ids": ",".join(str(x) for x in batch), "limit": 500},
            )
            for person in payload.get("data") or []:
                result[person["id"]] = person
        return result

    def all_organizations(self) -> list[dict]:
        """Every organisation, with won-deal counts and custom fields.

        Brand clustering has to see organisations that hold only closed
        business: a duplicate record carrying the won history often has no open
        deals, so fetching just the ones in the pipeline hides it.
        """
        return list(self._paginate_v2(
            "/api/v2/organizations",
            {"include_fields": "won_deals_count", "include_option_labels": "true"},
        ))

    def persons_by_org(self, org_ids: set[int]) -> dict[int, list[dict]]:
        """All contacts grouped by organization (for the Brands sheet)."""
        grouped: dict[int, list[dict]] = {org_id: [] for org_id in org_ids}
        for person in self._paginate_v2("/api/v2/persons"):
            org_id = person.get("org_id")
            if org_id in grouped:
                grouped[org_id].append(person)
        return grouped

    def stages(self, pipeline_id: int | None = None) -> list[dict]:
        params = {"pipeline_id": pipeline_id} if pipeline_id else {}
        return list(self._paginate_v2("/api/v2/stages", params))

    def users(self) -> dict[int, str]:
        payload = self._get("/v1/users")
        return {
            user["id"]: (user.get("name") or "").strip()
            for user in payload.get("data") or []
            if user.get("id")
        }

    # -- custom field discovery -------------------------------------------
    def _field_key(self, path: str, labels: tuple[str, ...], override: str = "") -> str | None:
        """Resolve a field key by display name.

        Matching is deliberately conservative: an over-eager match silently
        fills a report column with the wrong data, which is worse than leaving
        it blank. Order of preference is an explicit override, then an exact
        name match (custom fields first, since built-ins of the same name are
        usually the empty ones), then a whole-word match on a long label.
        """
        if override:
            return override

        payload = self._get(path, {"limit": 500})
        fields = [
            ((f.get("name") or "").strip().lower(), f.get("key"))
            for f in payload.get("data") or []
            if f.get("key")
        ]
        custom = [(n, k) for n, k in fields if CUSTOM_KEY.match(k or "")]

        for label in labels:
            for pool in (custom, fields):
                for name, key in pool:
                    if name == label:
                        return key

        for label in labels:
            if len(label) < MIN_FUZZY_LABEL:
                continue
            pattern = re.compile(rf"\b{re.escape(label)}\b")
            for pool in (custom, fields):
                for name, key in pool:
                    if pattern.search(name):
                        log.info("Matched %r to Pipedrive field %r on %s", label, name, path)
                        return key

        log.warning("No Pipedrive field on %s matched any of %s", path, labels)
        return None

    def field_catalogue(self) -> dict[str, list[dict]]:
        """Every deal/org field name and key, for diagnosing a failed match."""
        out: dict[str, list[dict]] = {}
        for label, path in (("deal", "/v1/dealFields"), ("organization", "/v1/organizationFields")):
            try:
                payload = self._get(path, {"limit": 500})
                out[label] = [
                    {"name": f.get("name"), "key": f.get("key"), "type": f.get("field_type")}
                    for f in payload.get("data") or []
                ]
            except PipedriveError as exc:
                out[label] = [{"error": str(exc)[:300]}]
        return out

    def field_keys(self, overrides: dict[str, str] | None = None) -> dict[str, str | None]:
        overrides = overrides or {}
        return {
            "deal_account_manager": self._field_key(
                "/v1/dealFields", DEAL_ACCOUNT_MANAGER_LABELS,
                overrides.get("deal_account_manager", ""),
            ),
            "org_industry": self._field_key(
                "/v1/organizationFields", ORG_INDUSTRY_LABELS,
                overrides.get("org_industry", ""),
            ),
            "org_sub_industry": self._field_key(
                "/v1/organizationFields", ORG_SUB_INDUSTRY_LABELS,
                overrides.get("org_sub_industry", ""),
            ),
        }
