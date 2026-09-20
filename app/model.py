"""Report data model: turns raw Pipedrive payloads into report-ready rows."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from .brand_history import BrandHistory
from .brands import Brand, is_placeholder, normalise_name, resolve_brands
from .team_directory import TeamDirectory

# Pipedrive stage name -> the friendlier label used in the newsletter.
STAGE_EMAIL_LABELS = {
    "Understand Need/Problem": "Discovery",
    "Create Proposal": "Preparing proposal",
    "Present Proposal": "Proposal presented",
    "Feedback": "Awaiting feedback",
    "Negotiation": "Negotiating",
    "IO Sent Out": "Contract sent",
}

EXISTING = "Existing client"
NEW_BUSINESS = "New business"
UNASSIGNED = "Unassigned"
BLANK = "-"


@dataclass(frozen=True)
class Stage:
    name: str
    order: int  # 1-based, matches the Settings sheet
    probability: float  # 0..1


@dataclass
class DealRow:
    brand: str
    title: str
    account_owner: str
    account_manager: str
    client_status: str
    stage: str
    stage_order: int
    value: float
    currency: str
    value_gbp: float
    probability: float
    weighted_gbp: float
    expected_close: date | None
    stage_changed: date | None
    added: date | None
    main_contact: str
    industry: str
    sub_industry: str
    website: str
    # True when no organisation was linked in Pipedrive and the brand name had
    # to be read off the deal title instead.
    brand_from_title: bool = False


@dataclass
class BrandRow:
    name: str
    client_status: str
    industry: str
    sub_industry: str
    website: str
    open_deals: int
    pipeline_gbp: float
    weighted_gbp: float
    furthest_stage: str
    account_owner: str
    account_manager: str
    contacts: list[str] = field(default_factory=list)
    brand_key: str = ""
    org_ids: list[int] = field(default_factory=list)
    is_duplicated: bool = False
    is_new_this_report: bool = False


@dataclass
class WonRow:
    """A deal won inside the reported financial year."""
    brand: str
    title: str
    account_owner: str
    won_on: date
    value_gbp: float


@dataclass
class ReportData:
    report_date: date
    rates: dict[str, float]
    rates_are_live: bool
    stages: list[Stage]
    deals: list[DealRow]
    brands: list[BrandRow]
    account_owners: list[str]
    account_managers: list[str]
    industries: list[str]
    directory: TeamDirectory = field(default_factory=TeamDirectory)
    history: BrandHistory = field(default_factory=BrandHistory)
    won_ytd: list[WonRow] = field(default_factory=list)
    financial_year_start: date | None = None

    # -- headline numbers --------------------------------------------------
    @property
    def open_deal_count(self) -> int:
        return len(self.deals)

    @property
    def pipeline_gbp(self) -> float:
        return sum(deal.value_gbp for deal in self.deals)

    @property
    def weighted_gbp(self) -> float:
        return sum(deal.weighted_gbp for deal in self.deals)

    @property
    def new_business_gbp(self) -> float:
        return sum(d.value_gbp for d in self.deals if d.client_status == NEW_BUSINESS)

    @property
    def new_brand_count(self) -> int:
        return sum(1 for brand in self.brands if brand.client_status == NEW_BUSINESS)

    @property
    def new_business_share(self) -> float:
        """New business as a share of total pipeline value, 0..1."""
        total = self.pipeline_gbp
        return (self.new_business_gbp / total) if total else 0.0

    @property
    def brands_new_this_report(self) -> list[BrandRow]:
        """Brands never included in a previous report, biggest first."""
        pool = [b for b in self.brands if b.is_new_this_report]
        pool.sort(key=lambda b: (-b.weighted_gbp, b.name.lower()))
        return pool

    @property
    def won_ytd_gbp(self) -> float:
        return sum(w.value_gbp for w in self.won_ytd)

    def ytd_by_owner(self) -> list[dict[str, Any]]:
        """Won value so far this financial year, per account owner.

        Owners are the same pod leads used for the pipeline, so the two charts
        read against each other: what landed, next to what is still in play.
        """
        totals: dict[str, dict[str, Any]] = {}
        for won in self.won_ytd:
            row = totals.setdefault(won.account_owner, {"name": won.account_owner, "deals": 0, "value_gbp": 0.0})
            row["deals"] += 1
            row["value_gbp"] += won.value_gbp
        # Everyone carrying pipeline appears even on a blank year, so a missing
        # bar reads as "nothing won yet" rather than "left the company".
        for owner in self.account_owners:
            totals.setdefault(owner, {"name": owner, "deals": 0, "value_gbp": 0.0})
        rows = sorted(totals.values(), key=lambda r: (-r["value_gbp"], r["name"].lower()))
        return rows

    @property
    def deals_missing_organisation(self) -> list[DealRow]:
        """Deals kept in the totals despite having no organisation linked.

        Worth surfacing: each one is a Pipedrive data gap, and its brand name
        is only as good as whatever the deal title leads with.
        """
        return [deal for deal in self.deals if deal.brand_from_title]

    def brand_records(self) -> list[dict]:
        """Rows for the history store, so these are not announced again."""
        return [
            {
                "brand_key": b.brand_key,
                "brand_name": b.name,
                "status": b.client_status,
                "org_ids": b.org_ids,
            }
            for b in self.brands
            if b.brand_key
        ]

    # -- breakdowns --------------------------------------------------------
    def by_stage(self) -> list[dict[str, Any]]:
        rows = []
        for stage in self.stages:
            deals = [d for d in self.deals if d.stage == stage.name]
            rows.append(
                {
                    "stage": stage.name,
                    "label": STAGE_EMAIL_LABELS.get(stage.name, stage.name),
                    "probability": stage.probability,
                    "deals": len(deals),
                    "value_gbp": sum(d.value_gbp for d in deals),
                    "weighted_gbp": sum(d.weighted_gbp for d in deals),
                }
            )
        return rows

    def _group(self, attr: str, keys: Iterable[str]) -> list[dict[str, Any]]:
        rows = []
        for key in keys:
            deals = [d for d in self.deals if getattr(d, attr) == key]
            rows.append(
                {
                    "name": key,
                    "deals": len(deals),
                    "value_gbp": sum(d.value_gbp for d in deals),
                    "weighted_gbp": sum(d.weighted_gbp for d in deals),
                }
            )
        return rows

    def by_owner(self) -> list[dict[str, Any]]:
        return self._group("account_owner", self.account_owners)

    def by_manager(self) -> list[dict[str, Any]]:
        return self._group("account_manager", self.account_managers)

    def by_industry(self) -> list[dict[str, Any]]:
        return self._group("industry", self.industries)

    def by_pod(self) -> list[dict[str, Any]]:
        """Each account owner's pod, and the account managers inside it.

        Rows are the owner followed by their account managers, so the Summary
        reads as the org chart rather than two unrelated lists. Managers with
        pipeline but no pod still appear, under 'Unassigned'.
        """
        placed: set[str] = set()
        rows: list[dict[str, Any]] = []

        for owner in self.account_owners:
            owner_deals = [d for d in self.deals if d.account_owner == owner]
            pod_managers = next(
                (p.account_managers for p in self.directory.pods if p.lead == owner), []
            )
            # Anyone actually carrying pipeline under this owner, pod or not.
            active = [
                m for m in self.account_managers
                if any(d.account_manager == m for d in owner_deals)
            ]
            managers = [m for m in pod_managers if m in active]
            managers += [m for m in active if m not in managers]

            rows.append(
                {
                    "owner": owner,
                    "manager": None,
                    "deals": len(owner_deals),
                    "value_gbp": sum(d.value_gbp for d in owner_deals),
                    "weighted_gbp": sum(d.weighted_gbp for d in owner_deals),
                }
            )
            for manager in managers:
                placed.add(manager)
                deals = [d for d in owner_deals if d.account_manager == manager]
                rows.append(
                    {
                        "owner": owner,
                        "manager": manager,
                        "deals": len(deals),
                        "value_gbp": sum(d.value_gbp for d in deals),
                        "weighted_gbp": sum(d.weighted_gbp for d in deals),
                    }
                )

        unplaced = [m for m in self.account_managers if m not in placed]
        for manager in unplaced:
            deals = [d for d in self.deals if d.account_manager == manager]
            rows.append(
                {
                    "owner": UNASSIGNED,
                    "manager": manager,
                    "deals": len(deals),
                    "value_gbp": sum(d.value_gbp for d in deals),
                    "weighted_gbp": sum(d.weighted_gbp for d in deals),
                }
            )
        return rows

    # -- newsletter lists --------------------------------------------------
    def by_stage_owner(self) -> dict[str, dict[str, float]]:
        """Each stage's unweighted value split by account owner.

        The newsletter stacks every stage bar by owner, so it needs the
        cross-tab that ``by_stage`` and ``by_owner`` each flatten away.
        """
        split: dict[str, dict[str, float]] = {}
        for stage in self.stages:
            owners: dict[str, float] = {}
            for deal in self.deals:
                if deal.stage == stage.name:
                    owners[deal.account_owner] = (
                        owners.get(deal.account_owner, 0.0) + deal.value_gbp
                    )
            split[stage.name] = owners
        return split

    def top_brands(self, status: str, limit: int = 10) -> list[BrandRow]:
        pool = [b for b in self.brands if b.client_status == status]
        pool.sort(key=lambda b: (-b.weighted_gbp, b.name.lower()))
        return pool[:limit]


# --------------------------------------------------------------------------
# Building the model from Pipedrive payloads
# --------------------------------------------------------------------------
def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _custom(entity: dict, key: str | None) -> Any:
    if not key:
        return None
    value = (entity.get("custom_fields") or {}).get(key)
    if isinstance(value, dict):
        return value.get("label") or value.get("value")
    if isinstance(value, list):
        labels = [item.get("label") for item in value if isinstance(item, dict) and item.get("label")]
        return ", ".join(labels) if labels else None
    return value


def _text(value: Any, default: str = BLANK) -> str:
    if value in (None, ""):
        return default
    return str(value).strip() or default


def _ordered_by_value(pairs: dict[str, float]) -> list[str]:
    """Names sorted by pipeline value descending, then alphabetically."""
    return [name for name, _ in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


# Deal titles are written "<Brand> x <campaign>" or "<Brand> SOW 1 - OCT 26",
# so the leading segment is a usable brand name when nobody linked an
# organisation to the deal.
_TITLE_SEPARATORS = (" x ", " X ", " - ", " \u2013 ", " \u2014 ", " / ", " | ")
_TITLE_TAIL = re.compile(r"\s+(sow|fy)\b.*$", re.IGNORECASE)


def brand_from_deal_title(title: str) -> str:
    """Best-effort brand name for a deal with no organisation linked.

    Dropping such a deal would silently under-report the stage and pipeline
    totals, so the report keeps it under whatever the title leads with. The
    result carries no brand key, so it can never be announced to John as a new
    brand or written to the history table.
    """
    full = _text(title, default="").strip()
    name = full
    for separator in _TITLE_SEPARATORS:
        head = name.split(separator, 1)[0].strip()
        if head:
            name = head
    name = _TITLE_TAIL.sub("", name).strip()
    return name or full


def build_report(
    *,
    report_date: date,
    rates: dict[str, float],
    rates_are_live: bool,
    stages_payload: list[dict],
    deals_payload: list[dict],
    orgs: dict[int, dict],
    persons: dict[int, dict],
    org_contacts: dict[int, list[dict]],
    users: dict[int, str],
    won_org_ids: set[int],
    field_keys: dict[str, str | None],
    directory: TeamDirectory | None = None,
    brands_by_org: dict[int, Brand] | None = None,
    history: BrandHistory | None = None,
    won_deals_payload: list[dict] | None = None,
    financial_year_start: date | None = None,
) -> ReportData:
    directory = directory or TeamDirectory()
    history = history or BrandHistory()
    # Duplicate organisation records split a brand's won history from its live
    # pipeline, so resolve organisations to brands before anything is counted.
    if brands_by_org is None:
        # Won history is pooled across the cluster, so a brand whose past deals
        # sit on one record and whose open deal sits on another still reads as
        # an existing client.
        brands_by_org = resolve_brands(orgs, {org_id: 1 for org_id in won_org_ids})
    stages = sorted(
        (
            Stage(
                name=stage["name"],
                order=int(stage.get("order_nr", 0)) + 1,
                probability=float(stage.get("deal_probability") or 0) / 100.0,
            )
            for stage in stages_payload
        ),
        key=lambda s: s.order,
    )
    stage_by_id = {stage["id"]: stage["name"] for stage in stages_payload}
    stage_meta = {stage.name: stage for stage in stages}

    am_key = field_keys.get("deal_account_manager")
    industry_key = field_keys.get("org_industry")
    sub_industry_key = field_keys.get("org_sub_industry")

    # An unlinked deal is matched back to a real brand only on an identical
    # normalised name - the same conservative rule the resolver uses, so no
    # fuzzy matching can invent a duplicate.
    brand_by_normalised: dict[str, Brand] = {}
    for resolved_brand in brands_by_org.values():
        brand_by_normalised.setdefault(normalise_name(resolved_brand.name), resolved_brand)

    deal_rows: list[DealRow] = []
    for deal in deals_payload:
        org_id = deal.get("org_id")
        org = orgs.get(org_id) or {}
        resolved = brands_by_org.get(org_id)
        brand = _text(resolved.name if resolved else org.get("name"), default="")
        brand_from_title = False
        if not brand:
            # Nobody linked an organisation to this deal. Keep it anyway - a
            # dropped deal quietly under-reports the stage and pipeline totals
            # - and read the brand off the deal title.
            brand = brand_from_deal_title(deal.get("title"))
            brand_from_title = True
            key = normalise_name(brand)
            match = None if is_placeholder(brand) else brand_by_normalised.get(key)
            if match is not None:
                # The title names a brand already in the pipeline, so the deal
                # joins it and inherits its real key and client status.
                resolved = match
                brand = resolved.name
        if not brand:
            continue  # nothing to report on: no organisation and no title

        stage_name = stage_by_id.get(deal.get("stage_id"))
        stage = stage_meta.get(stage_name)
        if stage is None:
            continue  # deal sits in a stage outside the reported pipeline

        currency = _text(deal.get("currency"), default="GBP").upper()
        rate = rates.get(currency, 1.0)
        value = float(deal.get("value") or 0)
        value_gbp = value * rate
        weighted_gbp = value_gbp * stage.probability

        # Account Manager comes from the Supabase directory, keyed by
        # organisation. A Pipedrive custom field is honoured if one exists.
        am_raw = _custom(deal, am_key)
        if isinstance(am_raw, (int, float)) and int(am_raw) in users:
            account_manager = users[int(am_raw)]
        else:
            account_manager = _text(am_raw)
        account_manager = directory.account_manager(deal.get("org_id"), account_manager)

        owner_id = deal.get("owner_id")
        account_owner = directory.account_owner(
            owner_id, _text(users.get(owner_id), default=UNASSIGNED)
        )

        person = persons.get(deal.get("person_id")) or {}

        deal_rows.append(
            DealRow(
                brand=brand,
                title=_text(deal.get("title"), default=""),
                account_owner=account_owner,
                account_manager=account_manager,
                client_status=(
                    EXISTING
                    if (resolved.is_existing_client if resolved else org_id in won_org_ids)
                    else NEW_BUSINESS
                ),
                stage=stage.name,
                stage_order=stage.order,
                value=value,
                currency=currency,
                value_gbp=value_gbp,
                probability=stage.probability,
                weighted_gbp=weighted_gbp,
                expected_close=_as_date(deal.get("expected_close_date")),
                stage_changed=_as_date(deal.get("stage_change_time")) or _as_date(deal.get("add_time")),
                added=_as_date(deal.get("add_time")),
                main_contact=_text(person.get("name"), default=""),
                industry=_text(_custom(org, industry_key), default=""),
                sub_industry=_text(_custom(org, sub_industry_key), default=""),
                website=_text(org.get("website"), default=""),
                brand_from_title=brand_from_title,
            )
        )

    # Most advanced stage first, then largest deal first - matches the template.
    deal_rows.sort(key=lambda d: (-d.stage_order, -d.value_gbp, d.brand.lower()))

    # -- brand roll-up -----------------------------------------------------
    # One row per resolved brand, so duplicate organisation records collapse
    # into a single line rather than appearing twice with split figures.
    brands: list[BrandRow] = []
    deals_by_brand: dict[str, list[DealRow]] = {}
    for deal in deal_rows:
        deals_by_brand.setdefault(deal.brand, []).append(deal)

    brand_by_name: dict[str, Brand] = {}
    for brand in brands_by_org.values():
        brand_by_name.setdefault(brand.name, brand)

    for name, brand_deals in deals_by_brand.items():
        resolved = brand_by_name.get(name)
        org_ids = resolved.org_ids if resolved else []
        top_deal = max(brand_deals, key=lambda d: (d.stage_order, d.value_gbp))
        # Contacts come from every organisation record behind the brand.
        contacts = [
            _text(person.get("name"), default="")
            for org_id in (org_ids or [None])
            for person in org_contacts.get(org_id, [])
            if _text(person.get("name"), default="")
        ]
        brand_key = resolved.key if resolved else ""
        brands.append(
            BrandRow(
                name=name,
                client_status=brand_deals[0].client_status,
                industry=next((d.industry for d in brand_deals if d.industry), ""),
                sub_industry=next((d.sub_industry for d in brand_deals if d.sub_industry), ""),
                website=next((d.website for d in brand_deals if d.website), ""),
                open_deals=len(brand_deals),
                pipeline_gbp=sum(d.value_gbp for d in brand_deals),
                weighted_gbp=sum(d.weighted_gbp for d in brand_deals),
                furthest_stage=max(brand_deals, key=lambda d: d.stage_order).stage,
                account_owner=top_deal.account_owner,
                account_manager=top_deal.account_manager,
                contacts=sorted(set(contacts), key=str.lower),
                brand_key=brand_key,
                org_ids=list(org_ids),
                is_duplicated=bool(resolved and resolved.is_duplicated),
                # Only genuinely new business counts as new to John; a returning
                # client is not news even the first time it is reported.
                is_new_this_report=(
                    brand_deals[0].client_status == NEW_BUSINESS
                    and bool(brand_key)
                    and history.is_new(brand_key)
                ),
            )
        )
    brands.sort(key=lambda b: (-b.pipeline_gbp, b.name.lower()))

    owner_totals: dict[str, float] = {}
    manager_totals: dict[str, float] = {}
    industry_totals: dict[str, float] = {}
    for deal in deal_rows:
        owner_totals[deal.account_owner] = owner_totals.get(deal.account_owner, 0.0) + deal.value_gbp
        if deal.account_manager != BLANK:
            manager_totals[deal.account_manager] = manager_totals.get(deal.account_manager, 0.0) + deal.value_gbp
        if deal.industry:
            industry_totals[deal.industry] = industry_totals.get(deal.industry, 0.0) + deal.value_gbp

    # -- won year to date --------------------------------------------------
    # Same owner mapping as the pipeline, so the two charts compare like with
    # like. A won deal whose organisation was deleted still counts: the money
    # landed, whatever happened to the record afterwards.
    won_rows: list[WonRow] = []
    for deal in won_deals_payload or []:
        won_on = _as_date(deal.get("won_time")) or _as_date(deal.get("local_won_date"))
        if won_on is None or (financial_year_start and won_on < financial_year_start):
            continue
        currency = _text(deal.get("currency"), default="GBP").upper()
        org_id = deal.get("org_id")
        resolved = brands_by_org.get(org_id)
        owner_id = deal.get("owner_id")
        won_rows.append(
            WonRow(
                brand=_text(
                    resolved.name if resolved else (orgs.get(org_id) or {}).get("name"),
                    default=brand_from_deal_title(deal.get("title")),
                ),
                title=_text(deal.get("title"), default=""),
                account_owner=directory.account_owner(
                    owner_id, _text(users.get(owner_id), default=UNASSIGNED)
                ),
                won_on=won_on,
                value_gbp=float(deal.get("value") or 0) * rates.get(currency, 1.0),
            )
        )
    won_rows.sort(key=lambda w: (-w.value_gbp, w.brand.lower()))

    return ReportData(
        report_date=report_date,
        rates=rates,
        rates_are_live=rates_are_live,
        stages=stages,
        deals=deal_rows,
        brands=brands,
        account_owners=_ordered_by_value(owner_totals),
        account_managers=_ordered_by_value(manager_totals),
        industries=_ordered_by_value(industry_totals),
        directory=directory,
        history=history,
        won_ytd=won_rows,
        financial_year_start=financial_year_start,
    )
