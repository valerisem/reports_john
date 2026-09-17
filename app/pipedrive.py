"""Thin Pipedrive REST client.

Custom fields are resolved by their human-readable name at runtime (via
``/dealFields`` and ``/organizationFields``) rather than by hard-coded hash
keys, so the app keeps working if the CRM is rebuilt or copied.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)

# Field labels we look for in Pipedrive. First match wins.
DEAL_ACCOUNT_MANAGER_LABELS = ("account manager", "am", "account mgr")
ORG_INDUSTRY_LABELS = ("industry",)
ORG_SUB_INDUSTRY_LABELS = ("sub-industry", "sub industry", "subindustry")


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

    def won_deal_org_ids(self, pipeline_id: int | None = None) -> set[int]:
        """Org ids with at least one won deal -> used for 'Existing client'."""
        params: dict[str, Any] = {"status": "won"}
        if pipeline_id:
            params["pipeline_id"] = pipeline_id
        return {
            deal["org_id"]
            for deal in self._paginate_v2("/api/v2/deals", params)
            if deal.get("org_id")
        }

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
    def _field_key(self, path: str, labels: tuple[str, ...]) -> str | None:
        payload = self._get(path, {"limit": 500})
        by_name = {
            (field.get("name") or "").strip().lower(): field.get("key")
            for field in payload.get("data") or []
        }
        for label in labels:
            if by_name.get(label):
                return by_name[label]
        # Fall back to a contains-match so "Industry (primary)" still resolves.
        for label in labels:
            for name, key in by_name.items():
                if label in name:
                    return key
        log.warning("No Pipedrive field found on %s for labels %s", path, labels)
        return None

    def field_keys(self) -> dict[str, str | None]:
        return {
            "deal_account_manager": self._field_key("/v1/dealFields", DEAL_ACCOUNT_MANAGER_LABELS),
            "org_industry": self._field_key("/v1/organizationFields", ORG_INDUSTRY_LABELS),
            "org_sub_industry": self._field_key("/v1/organizationFields", ORG_SUB_INDUSTRY_LABELS),
        }
