"""The team directory in Supabase: pods, account owners and account managers.

Pipedrive knows who *owns* a deal, but not the org chart. Supabase holds it:

* ``team``                 - people, with ``pd_id`` linking to a Pipedrive user,
                             ``pod_id`` pointing at their pod lead, and
                             ``left_date`` for leavers.
* ``roles``                - job titles; account managers are one role.
* ``account_manager_orgs`` - which account manager looks after which Pipedrive
                             organisation (``pd_org_id``).

Two lookups come out of this:

* **Account Owner** - the pod lead of whoever owns the deal in Pipedrive. A deal
  owned by Maggie Parrott reports under Valeriia Mukhai, because Maggie is in
  Valeriia's pod. This is why the report shows three owners where Pipedrive
  shows seven.
* **Account Manager** - taken from ``account_manager_orgs`` by organisation, and
  independent of the pod: an org in Carrick's pod can still be managed by an AM
  from Ritchie's.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import httpx

log = logging.getLogger(__name__)

ACCOUNT_MANAGER_ROLE = "account manager"


class TeamDirectoryError(RuntimeError):
    pass


@dataclass
class Member:
    id: int
    full_name: str
    role: str
    pod_id: int | None
    pipedrive_user_id: int | None
    left_date: date | None

    def is_active(self, on: date) -> bool:
        return self.left_date is None or self.left_date > on


@dataclass
class Pod:
    lead: str
    account_managers: list[str] = field(default_factory=list)


@dataclass
class TeamDirectory:
    """Resolved lookups, ready for the report."""

    owner_by_pipedrive_user: dict[int, str] = field(default_factory=dict)
    name_by_pipedrive_user: dict[int, str] = field(default_factory=dict)
    manager_by_org: dict[int, str] = field(default_factory=dict)
    pods: list[Pod] = field(default_factory=list)
    loaded: bool = False

    def account_owner(self, pipedrive_user_id: int | None, fallback: str) -> str:
        """Pod lead for a Pipedrive user, else their own proper name."""
        if pipedrive_user_id is None:
            return fallback
        return (
            self.owner_by_pipedrive_user.get(pipedrive_user_id)
            or self.name_by_pipedrive_user.get(pipedrive_user_id)
            or fallback
        )

    def account_manager(self, org_id: int | None, fallback: str) -> str:
        if org_id is None:
            return fallback
        return self.manager_by_org.get(org_id) or fallback

    def pod_for_manager(self, manager: str) -> str | None:
        for pod in self.pods:
            if manager in pod.account_managers:
                return pod.lead
        return None


def _as_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class SupabaseClient:
    def __init__(self, url: str, api_key: str, timeout: float = 30.0):
        if not url or not api_key:
            raise TeamDirectoryError("SUPABASE_URL and SUPABASE_KEY must both be set")
        self._client = httpx.Client(
            base_url=url.rstrip("/") + "/rest/v1",
            headers={"apikey": api_key, "Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SupabaseClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def rows(self, table: str, select: str) -> list[dict]:
        response = self._client.get(f"/{table}", params={"select": select, "limit": 10000})
        if response.status_code >= 400:
            raise TeamDirectoryError(
                f"GET {table} -> {response.status_code}: {response.text[:300]}"
            )
        return response.json()

    def rows_range(self, table: str, select: str, offset: int, limit: int) -> list[dict]:
        """One page of a table too large to read in a single response."""
        response = self._client.get(
            f"/{table}", params={"select": select, "limit": limit, "offset": offset}
        )
        if response.status_code >= 400:
            raise TeamDirectoryError(
                f"GET {table} -> {response.status_code}: {response.text[:300]}"
            )
        return response.json()


def build_directory(
    *,
    team_rows: list[dict],
    role_rows: list[dict],
    manager_org_rows: list[dict],
    on: date,
) -> TeamDirectory:
    role_names = {row["id"]: (row.get("name") or "").strip().lower() for row in role_rows}

    members: dict[int, Member] = {}
    for row in team_rows:
        member_id = _as_int(row.get("id"))
        if member_id is None:
            continue
        members[member_id] = Member(
            id=member_id,
            full_name=(row.get("full_name") or "").strip(),
            role=role_names.get(row.get("role_id"), ""),
            pod_id=_as_int(row.get("pod_id")),
            pipedrive_user_id=_as_int(row.get("pd_id")),
            left_date=_as_date(row.get("left_date")),
        )

    directory = TeamDirectory(loaded=True)

    for member in members.values():
        if member.pipedrive_user_id is None or not member.full_name:
            continue
        directory.name_by_pipedrive_user[member.pipedrive_user_id] = member.full_name
        lead = members.get(member.pod_id) if member.pod_id else None
        if lead and lead.full_name:
            directory.owner_by_pipedrive_user[member.pipedrive_user_id] = lead.full_name

    for row in manager_org_rows:
        org_id = _as_int(row.get("pd_org_id"))
        manager = members.get(_as_int(row.get("team_id")))
        if org_id is None or manager is None or not manager.full_name:
            continue
        # A leaver's accounts still belong to them until their last day; after
        # that the column is better left blank than wrong.
        if manager.is_active(on):
            directory.manager_by_org[org_id] = manager.full_name

    pods: dict[int, Pod] = {}
    for member in members.values():
        if member.pod_id is None or not member.is_active(on):
            continue
        lead = members.get(member.pod_id)
        if lead is None or not lead.full_name:
            continue
        pod = pods.setdefault(member.pod_id, Pod(lead=lead.full_name))
        if member.role == ACCOUNT_MANAGER_ROLE and member.full_name not in pod.account_managers:
            pod.account_managers.append(member.full_name)
    for pod in pods.values():
        pod.account_managers.sort(key=str.lower)
    directory.pods = sorted(pods.values(), key=lambda p: p.lead.lower())

    return directory


def load_directory(url: str, api_key: str, on: date) -> TeamDirectory:
    """Fetch the directory; an empty one is returned if Supabase is unreachable.

    A missing directory degrades the report (owners fall back to raw Pipedrive
    names, managers to "-") but never stops it going out.
    """
    try:
        with SupabaseClient(url, api_key) as client:
            directory = build_directory(
                team_rows=client.rows("team", "id,full_name,role_id,pod_id,pd_id,left_date"),
                role_rows=client.rows("roles", "id,name"),
                manager_org_rows=client.rows("account_manager_orgs", "pd_org_id,team_id,org_name"),
                on=on,
            )
        log.info(
            "Team directory: %d pods, %d owner mappings, %d managed orgs",
            len(directory.pods), len(directory.owner_by_pipedrive_user),
            len(directory.manager_by_org),
        )
        return directory
    except Exception as exc:  # noqa: BLE001 - never block the report
        log.warning("Team directory unavailable (%s); falling back to Pipedrive names", exc)
        return TeamDirectory(loaded=False)
